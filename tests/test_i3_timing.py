"""Controlled ownership transitions during a single Live observation."""
import asyncio
import unittest

from test_live import LiveTransport
from windows_observer_agent.live import observe_live
from windows_observer_agent.mcp_client import MCPClient


class ControlledTransport(LiveTransport):
    def __init__(self, statuses, pause=None):
        super().__init__()
        self.statuses = iter(statuses)
        self.pause = pause
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.worker = None

    async def call_tool(self, name, arguments):
        result = await super().call_tool(name, arguments)
        if name == self.pause:
            self.entered.set()
            self.worker = asyncio.create_task(self.release.wait())
            await asyncio.shield(self.worker)
        if name == 'ControlStatus':
            return {'structuredContent': next(self.statuses)}
        return result


def state(owner='ready', generation=1):
    return {'state': owner, 'generation': generation, 'wait_seconds': 0}


class ObservationTimingTests(unittest.IsolatedAsyncioTestCase):
    def assert_discarded(self, raw):
        self.assertFalse(raw.metadata['observation_complete'])
        self.assertFalse(raw.snapshot['success'])
        self.assertFalse(any(r['tool'] in ('Screenshot', 'Snapshot') and
                             not r['result'].get('isError') for r in raw.tool_results))

    async def test_initial_user(self):
        t = ControlledTransport([state('user')])
        self.assert_discarded(await observe_live(MCPClient(t)))
        self.assertNotIn('Screenshot', t.events)

    async def test_initial_unavailable_or_pending(self):
        for owner in ('unavailable', 'takeover_pending'):
            t = ControlledTransport([state(owner)])
            self.assert_discarded(await observe_live(MCPClient(t)))
            self.assertNotIn('Screenshot', t.events)

    async def test_takeover_during_screenshot(self):
        await self.takeover_during('Screenshot')

    async def test_takeover_during_snapshot(self):
        await self.takeover_during('Snapshot')

    async def takeover_during(self, tool):
        statuses = [state(), state('ai', 2)] if tool == 'Snapshot' else [state()]
        t = ControlledTransport(statuses, pause=tool)
        task = asyncio.create_task(observe_live(MCPClient(t)))
        await asyncio.wait_for(t.entered.wait(), 1)
        t.statuses = iter([state('user', 3)])
        t.release.set()
        raw = await task
        self.assert_discarded(raw)
        self.assertEqual(raw.control_status['owner'], 'user')
        self.assertEqual(t.events[-2:], ['ControlStatus', 'close'])
        if tool == 'Screenshot':
            self.assertNotIn('Snapshot', t.events)

    async def test_success_rechecks_both_captures(self):
        t = ControlledTransport([state(), state('ai', 2), state('ai', 2)])
        raw = await observe_live(MCPClient(t))
        self.assertTrue(raw.metadata['observation_complete'])
        self.assertEqual(t.events[2:-1], ['ControlStatus', 'Screenshot', 'ControlStatus',
                                          'Snapshot', 'ControlStatus'])
        self.assertEqual(raw.control_status['owner'], 'agent')

    async def test_takeover_between_tools(self):
        t = ControlledTransport([state(), state('takeover_pending', 3)])
        self.assert_discarded(await observe_live(MCPClient(t)))
        self.assertEqual(t.events.count('Screenshot'), 1)
        self.assertNotIn('Snapshot', t.events)

    async def timeout_during(self, name):
        t = ControlledTransport([state()], pause=name)
        try:
            raw = await observe_live(MCPClient(t, call_timeout=0.05))
            self.assert_discarded(raw)
            self.assertIn('close', t.events)
            self.assertNotIn('Snapshot', t.events)
            self.assertFalse(t.worker.done())
        finally:
            t.release.set()
            if t.worker:
                await t.worker

    async def test_control_status_timeout(self):
        await self.timeout_during('ControlStatus')

    async def test_screenshot_timeout_leaves_no_accepted_result(self):
        await self.timeout_during('Screenshot')

    async def test_invalid_or_missing_control_evidence(self):
        for invalid in ({'state': 'ready'}, state('ai', True), {'wrong': 'state'}):
            t = ControlledTransport([invalid])
            self.assert_discarded(await observe_live(MCPClient(t)))
            self.assertNotIn('Screenshot', t.events)

    async def test_cancellation_propagates(self):
        t = ControlledTransport([state()], pause='Screenshot')
        task = asyncio.create_task(observe_live(MCPClient(t)))
        await asyncio.wait_for(t.entered.wait(), 1)
        task.cancel()
        try:
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertIn('close', t.events)
            self.assertNotIn('Snapshot', t.events)
        finally:
            t.release.set()
            await t.worker

    async def test_remote_worker_can_finish_after_close(self):
        t = ControlledTransport([state()], pause='Screenshot')
        task = asyncio.create_task(observe_live(MCPClient(t)))
        await asyncio.wait_for(t.entered.wait(), 1)
        task.cancel()
        try:
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(t.events[-1], 'close')
            self.assertFalse(t.worker.done())
        finally:
            t.release.set()
            self.assertTrue(await t.worker)
        self.assertNotIn('Snapshot', t.events)

    async def test_cancel_before_result_delivery(self):
        t = ControlledTransport([state()], pause='Screenshot')
        task = asyncio.create_task(observe_live(MCPClient(t)))
        await asyncio.wait_for(t.entered.wait(), 1)
        t.release.set()
        await t.worker
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertNotIn('Snapshot', t.events)

    async def test_generation_jump_even_after_recovery(self):
        for latest in (state('ready', 5), state('ai', 4), state('ai', 1), state('ready', 0)):
            t = ControlledTransport([state(), latest])
            self.assert_discarded(await observe_live(MCPClient(t)))
            self.assertNotIn('Snapshot', t.events)

    async def test_server_refuses_new_call_after_valid_check(self):
        t = ControlledTransport([state(), state('ai', 2)])
        t.fail = 'Snapshot'
        raw = await observe_live(MCPClient(t))
        self.assertFalse(raw.metadata['observation_complete'])
        self.assertEqual(t.events.count('Snapshot'), 1)
        self.assertEqual(t.events[-1], 'close')

    async def test_postcheck_failure_discards_capture(self):
        t = ControlledTransport([state(), state('ai', 2), {'state': 'unknown'}])
        self.assert_discarded(await observe_live(MCPClient(t)))
        self.assertEqual(t.events.count('Snapshot'), 1)

    async def test_late_result_after_suppressed_timeout(self):
        class LateTransport(LiveTransport):
            async def call_tool(self, name, arguments):
                if name == 'Screenshot':
                    try:
                        await asyncio.Future()
                    except asyncio.CancelledError:
                        pass  # A dependency finished its work despite cancellation.
                return await super().call_tool(name, arguments)

        t = LateTransport()
        raw = await observe_live(MCPClient(t, call_timeout=0.05))
        self.assertEqual(raw.metadata['error_code'], 'timeout')
        self.assertNotIn('Snapshot', t.events)

    async def test_cancelled_caller_cannot_publish_late_response(self):
        class LateTransport(LiveTransport):
            async def call_tool(self, name, arguments):
                if name == 'Screenshot':
                    asyncio.current_task().cancel()
                    try:
                        await asyncio.sleep(0)
                    except asyncio.CancelledError:
                        pass
                return await super().call_tool(name, arguments)

        t = LateTransport()
        task = asyncio.create_task(observe_live(MCPClient(t)))
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertNotIn('Snapshot', t.events)
