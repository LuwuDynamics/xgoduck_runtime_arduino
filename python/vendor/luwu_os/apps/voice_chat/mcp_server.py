# -*- coding: utf-8 -*-
"""MCP 服务端 (最小实现, 对齐 RIG-Omni mcp_server.cc 的应答格式)

hello 声明 features.mcp 后, 服务器会发来 jsonrpc 请求:
  initialize   → 应答 serverInfo + capabilities.tools
  tools/list   → 将 IoT Thing 的方法导出为 MCP 工具
  tools/call   → 执行 IoT 方法并返回结果 (LLM 控制机器人的通道)

self.* 内置工具 (对齐 Omni mcp_server.cc, 按 common/user-only 分类):
  common (暴露给 LLM 对话调用):
    self.get_device_status        → 设备实时状态 (audio_speaker/screen/network/firmware_version)
    self.audio_speaker.set_volume → 设置扬声器音量 (0-100)
    self.set_language             → 设置显示/语音语言 (zh-CN / en-US)
  user-only (annotations.audience=user, 不暴露给 AI, 供小程序设备页调用):
    self.get_system_info → 系统信息
    self.reboot          → 重启系统
    self.set_aec         → 开/关自动打断 (端侧 AEC)
    self.screen.get_info → 屏幕信息 (宽/高/单色)

self.camera.* 相机工具 (对齐 Omni mcp_server.cc, LLM 调用):
  self.camera.take_photo → 拍照上传服务端 VLM 分析 (耗时数秒, executor 内执行)
  self.camera.preview    → 拍照并展示到屏幕
  self.camera.get_base64 → 拍照返回压缩 base64 JPEG (user-only, 小程序设备页经
                           网关 HTTP API 调用取图; 不暴露给 AI, 避免 base64 撑爆上下文)
服务端 MCP initialize 的 capabilities.vision 携带分析地址与 token。
"""

import json

SERVER_NAME = "luwu-os"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"


class McpServer:
    def __init__(self, thing_manager, self_provider=None):
        self.thing_manager = thing_manager
        self.self_provider = self_provider  # Application: 提供 self.* 内置工具

    def handle_message(self, payload) -> dict:
        """处理一条 jsonrpc 消息, 返回应答 dict (通知类返回 None)"""
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                return None
        if not isinstance(payload, dict):
            return None

        method = payload.get("method", "")
        msg_id = payload.get("id")
        params = payload.get("params") or {}

        if method == "initialize":
            # 服务端 capabilities.vision 下发视觉分析地址/token (同 Omni ParseCapabilities)
            capabilities = params.get("capabilities") or {}
            vision = capabilities.get("vision") or {}
            if isinstance(vision, dict) and vision.get("url") and \
                    self.self_provider is not None:
                self.self_provider.mcp_on_vision_config(
                    vision.get("url", ""), vision.get("token", ""))
            return self._reply(msg_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            })
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            # 对齐 Omni GetToolsList: user-only 工具默认不返回 (仅 common 暴露给 AI),
            # 小程序等请求方带 withUserTools=true 才返回全部工具。
            with_user = bool(params.get("withUserTools") or False)
            tools = self._build_tools(list_user_only=with_user)
            reply = self._reply(msg_id, {"tools": tools})
            try:
                size = len(json.dumps(reply, ensure_ascii=False).encode("utf-8"))
                print(f"[mcp] tools/list 应答: {len(tools)} 个工具, {size} 字节")
            except Exception:
                pass
            return reply
        if method == "tools/call":
            return self._handle_tool_call(msg_id, params)
        if method == "ping":
            return self._reply(msg_id, {})
        # 未知方法
        if msg_id is not None:
            return self._error(msg_id, -32601, f"Method not found: {method}")
        return None

    # ---------- 工具导出 ----------
    def _build_tools(self, list_user_only: bool = False) -> list:
        """构建 MCP tools 列表。

        list_user_only=False 时只返回 common 工具 (对齐 Omni mcp_server.cc:
        user-only 工具默认不暴露给 AI); 小程序等请求方传 withUserTools=true 才返回全部。
        """
        tools = []
        if self.self_provider is not None:
            camera_tools = (self._camera_tools()
                            if self.self_provider.mcp_camera_available() else [])
            for tool in self._self_tools() + camera_tools:
                if tool.get("user_only"):
                    if not list_user_only:
                        continue
                    # 对齐 Omni mcp_server.cc to_json: user-only 工具带 annotations.audience=user,
                    # 请求方据此不暴露给 AI (仅小程序设备设置页可见)。
                    tool = {k: v for k, v in tool.items() if k != "user_only"}
                    tool["annotations"] = {"audience": ["user"]}
                    tools.append(tool)
                else:
                    tools.append({k: v for k, v in tool.items() if k != "user_only"})
        for thing_name, thing in self.thing_manager.things.items():
            for method_name, method in thing.methods.items():
                properties = {}
                required = []
                for p in method["parameters"]:
                    # 短单位可由参数名说明。实际网关上限由部署配置决定；
                    # 预算检查必须包含 send_mcp_message 信封和 JSON 转义。
                    prop = {"type": {"number": "number", "boolean": "boolean",
                                     "string": "string"}.get(p.value_type, "number")}
                    if len(p.description) > 4:
                        prop["description"] = p.description
                    properties[p.name] = prop
                    if p.required:
                        required.append(p.name)
                tools.append({
                    "name": f"{thing_name}.{method_name}",
                    "description": method["description"],
                    "inputSchema": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                })
        return tools

    def _self_tools(self) -> list:
        """设备自身内置工具 (不经过 thing_manager)。

        每个元素带 user_only 标记:
          False → 对齐 Omni AddCommonTools (暴露给 LLM 对话调用)
          True  → 对齐 Omni AddUserOnlyTools (annotations.audience=user, 不暴露给 AI)
        """
        return [
            {
                "name": "self.get_device_status",
                "description": (
                    "Get real-time device status (speaker volume, battery, network, etc).\n"
                    "Use for: 1. Answering questions about current condition "
                    "(volume? battery? charging?) 2. First step before controlling the device"
                ),
                "inputSchema": {"type": "object", "properties": {}, "required": []},
                "user_only": False,
            },
            {
                "name": "self.audio_speaker.set_volume",
                "description": (
                    "Set speaker volume (0-100). If current volume is unknown, "
                    "call `self.get_device_status` first."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "volume": {"type": "integer", "minimum": 0, "maximum": 100,
                                   "description": "Volume 0-100"},
                    },
                    "required": ["volume"],
                },
                "user_only": False,
            },
            {
                "name": "self.get_system_info",
                "description": "Get the system information",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
                "user_only": True,
            },
            {
                "name": "self.reboot",
                "description": "Reboot the system",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
                "user_only": True,
            },
            {
                "name": "self.set_aec",
                "description": (
                    "Enable or disable server-side AEC (auto interrupt). "
                    "Takes effect immediately and persists."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "enable": {"type": "integer", "minimum": 0, "maximum": 1,
                                   "default": 0, "description": "1=开 0=关"},
                    },
                    "required": [],
                },
                "user_only": False,
            },
            {
                "name": "self.screen.get_info",
                "description": "Information about the screen, including width, height, etc.",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
                "user_only": True,
            },
        ]

    def _camera_tools(self) -> list:
        """相机工具 (描述对齐 Omni, LLM 据此决定何时拍照)"""
        return [
            {
                "name": "self.camera.take_photo",
                "description": "拍照并AI识别分析(看东西/读文字/数数量/描述场景等)\n"
                               "Args: `question` 对照片的具体问题或要求, 把用户意图完整表达, "
                               "如'桌上有什么?'、'读出纸上写的字'、'有几个人?在做什么?'",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "对照片的具体问题或要求"},
                    },
                    "required": ["question"],
                },
            },
            {
                "name": "self.camera.preview",
                "description": "拍照并显示到屏幕(用户说拍照时用, 无需AI分析)",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "self.camera.get_base64",
                "description": "拍照并返回 base64 编码的 320x180 JPEG "
                               "(体积自适应压缩, 不调用 VLM, 供小程序/服务端直接取图)",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
                "user_only": True,
            },
        ]

    def _handle_self_tool(self, name: str, arguments: dict):
        if name == "self.get_device_status":
            return self.self_provider.mcp_get_device_status()
        if name == "self.audio_speaker.set_volume":
            return self.self_provider.mcp_set_volume(arguments.get("volume", 0))
        if name == "self.set_language":
            return self.self_provider.mcp_set_language(arguments.get("language", ""))
        if name == "self.get_system_info":
            return self.self_provider.mcp_get_system_info()
        if name == "self.reboot":
            return self.self_provider.mcp_reboot()
        if name == "self.set_aec":
            return self.self_provider.mcp_set_aec(arguments.get("enable", 0))
        if name == "self.screen.get_info":
            return self.self_provider.mcp_screen_get_info()
        if name == "self.camera.take_photo":
            return self.self_provider.mcp_take_photo(arguments.get("question", ""))
        if name == "self.camera.preview":
            return self.self_provider.mcp_preview_photo()
        if name == "self.camera.get_base64":
            return self.self_provider.mcp_camera_get_base64()
        raise ValueError(f"Unknown tool: {name}")

    # 阻塞型工具 (需等真实结果, 放 executor 避免卡 asyncio 事件循环)
    # 手势是唯一保留的本地视觉 (低延迟+确定性); 描述类场景走 self.camera.take_photo (VLM)
    _BLOCKING_TOOLS = {
        "Robot.FindBall", "Robot.CatchBall", "Robot.ReleaseBall", "Robot.FindPerson",
        "Robot.RecognizeGesture",
    }

    def _is_blocking_tool(self, name: str) -> bool:
        return (name.startswith(("self.camera.", "Duck.")) or name in self._BLOCKING_TOOLS
                or name in ('self.audio_speaker.set_volume', 'self.get_device_status', 'self.get_system_info'))

    async def handle_message_async(self, payload, run_blocking) -> dict:
        """异步入口: 阻塞型工具 (拍照/抓球/找球/寻人/放球) 放 executor, 不卡事件循环。
        其余工具走同步 handle_message (内部多以后台线程立即返回)。
        """
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                return None
        if not isinstance(payload, dict):
            return None
        if payload.get("method") == "tools/call":
            params = payload.get("params") or {}
            tool_name = params.get("name", "")
            if self._is_blocking_tool(tool_name):
                msg_id = payload.get("id")
                try:
                    return await run_blocking(
                        None, self._handle_tool_call, msg_id,
                        {"name": tool_name, "arguments": params.get("arguments") or {}})
                except Exception as e:
                    return self._error(msg_id, -32603, str(e))
        return self.handle_message(payload)

    # ---------- 工具调用 ----------
    def _handle_tool_call(self, msg_id, params: dict) -> dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}
        print(f"[mcp] tools/call: {tool_name} args={json.dumps(arguments, ensure_ascii=False)}")
        if tool_name.startswith("self."):
            if self.self_provider is None:
                return self._error(msg_id, -32602, f"Unknown tool: {tool_name}")
            try:
                result = self._handle_self_tool(tool_name, arguments)
                text = self._format_tool_text(result)
                return self._reply(msg_id, {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                })
            except Exception as e:
                return self._error(msg_id, -32603, str(e))
        if "." not in tool_name:
            return self._error(msg_id, -32602, f"Unknown tool: {tool_name}")
        thing_name, method_name = tool_name.split(".", 1)
        command = {
            "name": f"{thing_name}.{method_name}",
            "method": method_name,
            "properties": arguments,
        }
        try:
            result = self.thing_manager.invoke(command)
            text = json.dumps(result, ensure_ascii=False)
            tool_result = {
                "content": [{"type": "text", "text": text}],
                "isError": result.get("status") != "success",
            }
            if tool_result["isError"]:
                # Keep standard MCP content, and provide the error field read
                # by the deployed Xiaozhi device-MCP handler on both clouds.
                tool_result["error"] = str(result.get("message") or result.get("error") or "Tool execution failed")
                print(f"[mcp] tools/call rejected: {tool_name}: {tool_result['error']}")
            return self._reply(msg_id, tool_result)
        except Exception as e:
            return self._error(msg_id, -32603, str(e))

    # ---------- 应答构造 ----------
    @staticmethod
    def _format_tool_text(result) -> str:
        """把工具返回结果格式化为 MCP text 字段 (对齐 Omni mcp_server.cc Call()):

        - bool → "true"/"false"
        - int/float → 数字字符串
        - str → 原样 (已是最终文本)
        - dict/list → JSON 字符串
        """
        if isinstance(result, bool):
            return "true" if result else "false"
        if isinstance(result, (int, float)):
            return str(result)
        if isinstance(result, str):
            return result
        if isinstance(result, (list, dict)):
            return json.dumps(result, ensure_ascii=False)
        return str(result)

    @staticmethod
    def _reply(msg_id, result) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id, code, message) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": code, "message": message}}
