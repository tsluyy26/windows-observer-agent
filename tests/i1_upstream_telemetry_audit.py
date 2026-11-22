"""Optional, offline audit of an installed Windows-MCP 0.8.8 environment.

Run with that environment's python.exe -I -B -S. Normal test discovery does
not need the external server installation. Desktop, input hooks, server
transport and HTTP delivery are replaced before production paths can run.
"""
import ast
import asyncio
from contextlib import asynccontextmanager, ExitStack
from importlib.metadata import distributions
from importlib.util import find_spec
import json
import logging
import os
from pathlib import Path
import platform
import secrets
import sys
from tempfile import TemporaryDirectory
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid


def module(name, **attrs):
    result = ModuleType(name)
    result.__path__ = []
    vars(result).update(attrs)
    return result


def load_source(path, namespace, functions=None):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    if functions is not None:
        tree.body = [node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name in functions]
        if {node.name for node in tree.body} != set(functions):
            raise RuntimeError("Expected installed upstream function was not found")
    exec(compile(tree, str(path), "exec"), namespace)


class InertContext(SimpleNamespace):
    """Only the MCP client metadata read by the analytics decorator."""


class CapturedServer:
    def __init__(self, *, lifespan, **kwargs):
        self.lifespan = lifespan
        self.tools = {}

    def add_middleware(self, middleware):
        pass

    def tool(self, *, name, **kwargs):
        def register(function):
            self.tools[name] = function
            return function
        return register


class Audit(unittest.TestCase):
    def setUp(self):
        self.stack = self.enterContext(ExitStack())
        self.home = Path(self.enterContext(TemporaryDirectory(dir=AUDIT_HOME)))
        self.id_file = self.home / ".windows-mcp" / ".windows-mcp-user-id"
        self.key = token()
        self.markers = {key: token() for key in (
            "user", "path", "api_key", "mcp_token", "error", "ui",
            "title", "args", "image", "control", "local",
        )}
        self.markers["path"] = str(self.home / self.markers["path"] / "synthetic.py")
        self.stack.enter_context(patch.dict(os.environ, {
            "USERPROFILE": str(self.home), "HOME": str(self.home), "POSTHOG_API_KEY": self.key,
        }))
        self.wire = []
        self.status = 200
        self.stack.enter_context(patch.object(requests.adapters.HTTPAdapter, "send", self._send))
        # Skip the OS proxy registry and its proxy-bypass DNS probes. This audit
        # tests what the SDK submits to its HTTP adapter under an empty proxy map.
        self.stack.enter_context(patch.object(requests.sessions, "get_environ_proxies", return_value={}))
        # Keep SDK field assembly real without the platform fallback to cmd /c ver.
        self.stack.enter_context(patch.object(platform, "win32_ver", return_value=("11-synthetic", "", "", "")))
        self.base_hook = Mock()
        self.base_thread_hook = Mock()
        self.stack.enter_context(patch.object(sys, "excepthook", self.base_hook))
        self.stack.enter_context(patch.object(threading, "excepthook", self.base_thread_hook))

        parent = module("windows_mcp")
        infra = module("windows_mcp.infrastructure")
        config = module("windows_mcp.infrastructure.config")
        thread = module("windows_mcp.infrastructure.thread_affinity", run_uia_affine=asyncio.to_thread)
        analytics = module("windows_mcp.infrastructure.analytics")
        desktop = module("windows_mcp.desktop", control_overlay=Mock())
        self.controller = Mock()
        self.controller.status.return_value = {"state": "ready"}
        control = module("windows_mcp.desktop.control", get_controller=lambda: self.controller)
        self.desktop = Mock()
        desktop_service = module("windows_mcp.desktop.service", Desktop=self.desktop)
        notifications = module("windows_mcp.tools.control_notifications",
                               ControlNotifier=Mock(return_value=SimpleNamespace(start=Mock(), close=async_noop)),
                               ControlToolGate=Mock())
        tools = module("windows_mcp.tools")
        modules = (parent, infra, config, thread, analytics, desktop, control, desktop_service, notifications, tools)
        self.stack.enter_context(patch.dict(sys.modules, {item.__name__: item for item in modules}))

        load_source(PACKAGE / "infrastructure/config.py", vars(config))
        self.assertEqual(config.CONFIG_DIR, self.id_file.parent)
        # Context is a data-only substitute. Avoid loading FastMCP's Windows
        # server modules, which require pywin32's site startup hooks under -S.
        with patch.dict(vars(fastmcp), {"Context": InertContext}):
            load_source(PACKAGE / "infrastructure/analytics.py", vars(analytics))
        self.analytics_type = analytics.PostHogAnalytics
        self.analytics_created = []
        infra.PostHogAnalytics = self.make_analytics
        self.with_analytics = analytics.with_analytics
        self.assertEqual(self.analytics_created, [])
        self.assertFalse(self.id_file.exists())

        self.capture = Mock(return_value={k: self.markers[k] for k in ("ui", "title", "image", "control")})
        tool_args = dict(
            ToolAnnotations=Mock(), Context=InertContext, logger=logging.getLogger("audit.tools"),
            with_analytics=self.with_analytics, _as_bool=bool, _as_region=lambda value: value,
            capture_desktop_state=self.capture, build_snapshot_response=lambda result, **options: result,
        )
        load_source(PACKAGE / "tools/snapshot.py", tool_args, ("register",))
        control_args = dict(ToolAnnotations=Mock(), get_controller=control.get_controller)
        load_source(PACKAGE / "tools/control_status.py", control_args, ("register",))
        tools._MODULES = [SimpleNamespace(register=fn["register"]) for fn in (tool_args, control_args)]
        load_source(PACKAGE / "tools/__init__.py", vars(tools), ("register_all",))
        self.upstream = dict(
            asynccontextmanager=asynccontextmanager, FastMCP=CapturedServer, asyncio=asyncio, os=os,
            threading=threading, logger=logging.getLogger("audit.entry"), _mcp=None, analytics=None,
            instructions="", _control_config=config.ControlConfig(), _start_watchdog=lambda desktop: None,
        )
        load_source(PACKAGE / "__main__.py", self.upstream, ("_build_mcp", "_get_analytics", "_get_desktop"))
        capture = posthog.Posthog.capture
        self.events = self.stack.enter_context(patch.object(
            posthog.Posthog, "capture", autospec=True, side_effect=capture))

    def make_analytics(self):
        instance = self.analytics_type()
        self.analytics_created.append(instance)
        # Also close on test failures, before removing the fake transport.
        if instance.client:
            self.addCleanup(instance.client.shutdown)
        return instance

    def _send(self, request, **kwargs):
        self.assertEqual(request.headers.get("Content-Type"), "application/json")
        self.assertTrue(request.headers.get("User-Agent"))
        self.assertNotIn("Content-Encoding", request.headers)
        self.assertNotIn("Authorization", request.headers)
        self.assertNotIn("Proxy-Authorization", request.headers)
        self.assertTrue(kwargs["verify"])
        body = json.loads(request.body)
        self.assertTrue(body.get("api_key") == self.key, "Expected synthetic project key only")
        self.wire.append((request.method, request.url, body))
        response = requests.Response()
        response.status_code = self.status
        response._content = (b'{"featureFlags":{"i1-audit-flag":true},"errorsWhileComputingFlags":false}'
                             if request.url.endswith("/flags/?v=2") else b'{"status":"synthetic"}')
        response.request = request
        return response

    def packets(self):
        for method, url, body in self.wire:
            self.assertEqual((method, url), ("POST", "https://us.i.posthog.com/batch/"))
            self.assertIn("sent_at", body)
        return [event for _, _, body in self.wire for event in body["batch"]]

    def test_switch_matrix_and_actual_registered_tools(self):
        async def check():
            for index, (value, enabled) in enumerate((
                ("false", False), ("true", True), (None, True),
                ("invalid", True), ("FaLsE", False), (" false ", True),
            )):
                case_dir = self.home / f"case-{index}"
                case_id = case_dir / self.id_file.name
                with self.subTest(value=value), patch.dict(os.environ), \
                     patch.object(self.analytics_type, "USER_DATA_DIR", case_dir):
                    if value is None:
                        os.environ.pop("ANONYMIZED_TELEMETRY", None)
                    else:
                        os.environ["ANONYMIZED_TELEMETRY"] = value
                    self.upstream.update(_mcp=None, analytics=None)
                    before = len(self.analytics_created)
                    app = self.upstream["_build_mcp"]()
                    self.assertEqual(len(self.analytics_created), before)
                    self.assertEqual(sys.excepthook, self.base_hook)
                    self.assertFalse(case_id.exists())
                    async with app.lifespan(app):
                        self.assertEqual(len(self.analytics_created) - before, int(enabled))
                        self.assertEqual(sys.excepthook is not self.base_hook, enabled)
                        self.assertEqual(threading.excepthook is not self.base_thread_hook, enabled)
                        self.assertEqual(case_id.is_file(), enabled)
                        self.assertEqual(set(app.tools), {"Snapshot", "Screenshot", "ControlStatus"})
                        self.assertFalse(self.events.called)
                        for name in ("Snapshot", "Screenshot"):
                            result = await app.tools[name](region=self.markers["args"])
                            self.assertTrue(result["image"] == self.markers["image"])
                            self.capture.side_effect = RuntimeError(self.markers["error"])
                            result = await app.tools[name]()
                            self.capture.side_effect = None
                            self.assertTrue(self.markers["error"] in result[0])
                        self.assertEqual(app.tools["ControlStatus"](), {"state": "ready"})
                    self.assertFalse(self.events.called)
                    self.assertEqual(self.wire, [])
                    self.assertEqual(case_id.is_file(), enabled)
                    self.assertEqual(sys.excepthook, self.base_hook)
        LOOP.run(check())

    def test_disabled_does_not_read_or_replace_existing_synthetic_id(self):
        self.id_file.parent.mkdir()
        old = str(uuid.uuid4())
        self.id_file.write_text(old, encoding="utf-8")
        original = Path.read_text

        def read(path, *args, **kwargs):
            if path == self.id_file:
                raise AssertionError("Disabled telemetry accessed the synthetic ID")
            return original(path, *args, **kwargs)

        async def run():
            app = self.upstream["_build_mcp"]()
            async with app.lifespan(app):
                self.assertFalse(self.analytics_created)

        with patch.object(Path, "read_text", read):
            LOOP.run(run())
        self.assertTrue(self.id_file.read_text(encoding="utf-8") == old)
        self.assertFalse(self.events.called)
        self.assertEqual(self.wire, [])

    def test_explicit_sender_uses_pseudonym_and_ships_unfiltered_error_message(self):
        async def send():
            analytics = self.make_analytics()
            info = SimpleNamespace(name="synthetic-client", version="0.synthetic")
            ctx = InertContext(session=SimpleNamespace(client_params=SimpleNamespace(clientInfo=info)))
            marks = self.markers

            @self.with_analytics(analytics, "Snapshot")
            async def successful(**kwargs):
                return {k: marks[k] for k in ("ui", "title", "image", "control")}

            @self.with_analytics(analytics, "Snapshot")
            async def failing(**kwargs):
                raise RuntimeError(" ".join(marks[k] for k in ("user", "path", "api_key", "mcp_token", "error")))

            self.assertTrue(self.id_file.is_file())
            await successful(ctx=ctx, region=marks["args"])
            with self.assertRaises(RuntimeError):
                await failing(ctx=ctx, region=marks["args"])
            first = self.id_file.read_text(encoding="utf-8")
            self.assertEqual(uuid.UUID(first).version, 7)
            await analytics.close()
            second = self.make_analytics()
            self.assertTrue(second.user_id == first, "Persisted UUID was not reused")
            await second.close()
            self.id_file.unlink()
            third = self.make_analytics()
            self.assertTrue(third.user_id != first, "A deleted UUID was not regenerated")
            await third.close()
            return first

        identity = LOOP.run(send())
        events = {event["event"]: event for event in self.packets()}
        self.assertEqual(set(events), {"tool_executed", "exception"})
        for event in events.values():
            self.assertTrue(event["distinct_id"] == identity, "Unexpected analytics identity")
            self.assertIn("timestamp", event)
            self.assertIn("uuid", event)
            props = event["properties"]
            self.assertEqual(props["client_name"], "synthetic-client")
            self.assertEqual(props["client_version"], "0.synthetic")
            self.assertTrue(props["process_person_profile"])
            self.assertNotIn("$geoip_disable", props)
            for key in ("$os", "$os_version", "$python_runtime", "$python_version", "$lib", "$lib_version"):
                self.assertIn(key, props)
            payload = json.dumps(event)
            self.assertFalse(any(self.markers[k] in payload for k in ("ui", "title", "args", "image", "control")),
                             "Raw tool value reached the sender")
        error = events["exception"]["properties"]
        self.assertTrue(error["exception"] == error["traceback"], "Unexpected manual error stack format")
        for key in ("user", "path", "api_key", "mcp_token", "error"):
            self.assertTrue(self.markers[key] in error["exception"], "Raw exception message was filtered")
        print("I1_EVENT_PROPERTY_KEYS", {kind: sorted(event["properties"]) for kind, event in events.items()})

    def test_early_startup_failure_and_process_exit_hook(self):
        async def start_failure():
            # The real lifecycle constructs analytics before Desktop() and before its try/finally.
            def failing_desktop():
                unrelated_local = self.markers["local"]
                assert unrelated_local
                raise RuntimeError(self.markers["error"])

            self.desktop.side_effect = failing_desktop
            app = self.upstream["_build_mcp"]()
            async with app.lifespan(app):
                self.fail("Startup unexpectedly reached its body")

        def report_failure():
            try:
                LOOP.run(start_failure())
            except RuntimeError as error:
                sys.excepthook(type(error), error, error.__traceback__)
            else:
                self.fail("Expected the synthetic startup exception")

        report_failure()
        self.assertFalse(self.analytics_created)
        self.assertFalse(self.events.called)
        self.assertEqual(self.wire, [])

        with patch.dict(os.environ, {"ANONYMIZED_TELEMETRY": "true"}):
            self.upstream.update(_mcp=None, analytics=None)
            report_failure()
            instance = self.analytics_created[0]
            self.assertIsNot(sys.excepthook, self.base_hook)
            # Simulate interpreter exit while the fake HTTP transport is still installed.
            instance.client._atexit()
            hook_events = self.packets()
            self.assertEqual([event["event"] for event in hook_events], ["$exception"])
            frames = hook_events[0]["properties"]["$exception_list"][0]["stacktrace"]["frames"]
            self.assertTrue(any(Path(frame.get("abs_path") or "").name == "__main__.py" for frame in frames))
            self.assertFalse(any("vars" in frame for frame in frames))
            payload = json.dumps(hook_events)
            self.assertTrue(self.markers["error"] in payload, "Uncaught exception message absent")
            self.assertFalse(self.markers["local"] in payload, "Unrelated local marker was captured")
            self.assertTrue(self.id_file.is_file())
            self.assertTrue(hook_events[0]["distinct_id"] != self.id_file.read_text(encoding="utf-8"),
                            "Automatic hook reused the wrapper identity")
            LOOP.run(instance.close())

    def test_exhausted_http_retries_do_not_persist_event_queue(self):
        from posthog import consumer

        async def run():
            analytics = self.make_analytics()
            await analytics.track_tool("Synthetic", {"duration_ms": 1, "success": True})
            await analytics.close()

        self.status = 503
        with patch.object(consumer, "_backoff") as backoff:
            LOOP.run(run())
        self.assertEqual(backoff.call_count, 3)
        packets = self.packets()
        self.assertEqual(len(packets), 4)
        self.assertEqual(len({packet["uuid"] for packet in packets}), 1)
        self.assertEqual(list(self.id_file.parent.iterdir()), [self.id_file])

    def test_dormant_feature_flag_helper_and_separate_sdk_route(self):
        async def run():
            analytics = self.make_analytics()
            try:
                with self.assertRaisesRegex(AttributeError, "is_feature_enabled"):
                    await analytics.is_feature_enabled("i1-audit-flag")
                self.assertEqual(self.wire, [])
                # Test the installed SDK entry directly. Windows-MCP's dormant
                # wrapper calls the old name and does not reach this route.
                identity = analytics.user_id
                with self.assertWarns(DeprecationWarning):
                    enabled = analytics.client.feature_enabled("i1-audit-flag", identity)
                self.assertTrue(enabled)
                return identity
            finally:
                await analytics.close()

        identity = LOOP.run(run())
        checks = [(method, payload) for method, url, payload in self.wire
                  if url == "https://us.i.posthog.com/flags/?v=2"]
        self.assertEqual(len(checks), 1)
        method, body = checks[0]
        self.assertEqual(method, "POST")
        self.assertTrue(body["distinct_id"] == identity, "Feature flag identity differs")

    def test_fastmcp_instrumentation_has_no_configured_exporter(self):
        from fastmcp.telemetry import get_tracer, telemetry_mode

        self.assertEqual(telemetry_mode(), "native")
        self.assertIsNone(find_spec("opentelemetry.sdk"))
        with get_tracer().start_as_current_span("synthetic") as span:
            self.assertFalse(span.is_recording())
        self.assertFalse(self.events.called)
        self.assertEqual(self.wire, [])


async def async_noop():
    return None


def token():
    return "I1_SYNTHETIC_" + secrets.token_hex(20)


def deny_network_and_processes(event, arguments):
    if event in ("socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.gethostbyname",
                 "subprocess.Popen", "os.system", "os.startfile"):
        BLOCKED_OPERATIONS.append(event)
        raise RuntimeError("Audit denied a network or process operation")


if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise SystemExit("Auditing the installed distribution requires -I -B -S.")
    site_packages = Path(sys.prefix) / "Lib" / "site-packages"
    versions = {dist.metadata["Name"].lower(): dist.version for dist in distributions(path=[str(site_packages)])}
    if versions.get("windows-mcp") != "0.8.8":
        raise SystemExit("Expected the installed Windows-MCP 0.8.8 interpreter.")
    PACKAGE = site_packages / "windows_mcp"
    sys.path.append(str(site_packages))
    # Windows' event loop needs a private loopback socket pair. Create it before
    # enabling the network guard and before any third-party package is loaded.
    with asyncio.Runner() as LOOP, TemporaryDirectory() as temporary:
        LOOP.get_loop()
        BLOCKED_OPERATIONS = []
        sys.addaudithook(deny_network_and_processes)
        AUDIT_HOME = Path(temporary)
        working_directory = Path.cwd()
        os.chdir(temporary)
        safe_env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
        safe_env.update(USERPROFILE=temporary, HOME=temporary, LOCALAPPDATA=temporary, APPDATA=temporary,
                        TMP=temporary, TEMP=temporary, ANONYMIZED_TELEMETRY="false",
                        FASTMCP_ENV_FILE=r"\\.\NUL", POSTHOG_API_KEY=token())
        with patch.dict(os.environ, safe_env, clear=True):
            logging.disable(sys.maxsize)
            try:
                import requests
                import posthog
                import fastmcp

                print("Installed versions", {key: versions.get(key) for key in (
                    "windows-mcp", "posthog", "fastmcp", "opentelemetry-api", "requests", "uuid7")})
                result = unittest.main(exit=False, verbosity=2).result
                print("Network or process guard violations", len(BLOCKED_OPERATIONS),
                      sorted(set(BLOCKED_OPERATIONS)))
            finally:
                os.chdir(working_directory)
    sys.exit(not result.wasSuccessful() or bool(BLOCKED_OPERATIONS))
