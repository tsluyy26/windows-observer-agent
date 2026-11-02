# Windows Observer Agent v0.1 设计与实施计划

日期：2026-11-01。依据用户给定的离线范围直接实施，不接入真实桌面。

## 设计

采用标准库 dataclasses + JSON + unittest，零第三方运行依赖。相比引入
Pydantic/异步 MCP SDK，这一方案更易学习且无需联网安装包；相比单文件脚本，
按职责分模块能保留未来适配真实协议的边界。

数据流：MockClient → observe → build_scene → make_plan → render_report。
MockClient 只读包内 fixture 或用户明确提供的本地 Mock JSON，不创建进程或网络连接。
Snapshot/ControlStatus 是自定义 Mock 合约，不声称兼容 Windows-MCP 0.8.8。
截图仅保存模拟引用，不读取或上传图片。每个窗口和控件保留 JSON 指针来源。

Scene 保留原始观察、时间、桌面/显示器、活动窗口、已识别窗口、控件、
控制状态、错误及未知项。缺失字段用 None，不把未观察到等同于不存在。
500 为本项目 Mock UI 元素上限；达到上限即保守认为可能截断。
观察失败、控制权非 agent 时阻塞计划；其余步骤仍需未来重新观察和验证。
只支持明确列出的新建空白记事本目标，其他目标返回 unsupported。
不根据窗口标题推断文档为空；不生成关闭/覆盖/保存用户文档的步骤。

## 文件责任与执行顺序

- [x] UV 初始化独立目录，不创建子 Git 仓库或修改父仓库配置。
- [x] tests/test_agent.py：先编写七项验收与失败、未知状态、CLI 安全测试，运行确认缺失实现。
- [x] models.py：Observation、Scene、Window、UIControl、ControlState、Plan/PlanStep。
- [x] mcp_client.py / mock_data/normal.json：只读接口和明确标记的 Mock 数据。
- [x] observer.py / control_gate.py：采集和严格控制状态解析，不夺取控制权。
- [x] scene_model.py：容错转换、截断判断与证据追踪。
- [x] planner.py：目标白名单、前置条件、操作描述、验证和失败停止条件。
- [x] reporter.py / __main__.py：中文终端报告和可选 JSON 输出、明确的错误退出码。
- [x] README.md：数据合约、运行测试命令、安全边界和未来只读接入方案。
- [x] 用 Python 3.14.10 在项目虚拟环境运行测试、CLI 和语法编译检查，回读结果。

验收：用户列出的七类测试全部通过；识别到按钮仍为 click_verified=None；
CLI 调用期间审计钩子拦截任何进程/网络尝试；仅项目目录产生代码、缓存和测试文件。

验证结果：17 项 unittest 通过；正常、未观察到记事本、用户控制三种 CLI 场景
分别返回 conditional/conditional/blocked；UTF-8 中文终端输出正常。
项目当前无需构建分发包，直接从根目录运行 Python 包。
