# -*- coding: utf-8 -*-
"""IoT Thing 基类与描述符生成

对齐小智协议 iot 消息格式 (descriptor/state), 参考 xiaozhi_test/src/iot/thing.py。
"""

import json


class ValueType:
    BOOLEAN = "boolean"
    NUMBER = "number"
    STRING = "string"


class Parameter:
    """方法参数描述"""

    def __init__(self, name, description, value_type, required=True):
        self.name = name
        self.description = description
        self.value_type = value_type
        self.required = required
        self._value = None

    def set_value(self, value):
        self._value = value

    def get_value(self):
        return self._value

    def to_dict(self):
        return {
            "name": self.name,
            "description": self.description,
            "type": self.value_type,
            "required": self.required,
        }


class Property:
    """状态属性描述"""

    def __init__(self, name, description, getter):
        self.name = name
        self.description = description
        self.getter = getter

    def get_value(self):
        try:
            return self.getter()
        except Exception:
            return None


class Thing:
    """物联网设备基类: 注册属性(状态)与方法(命令)"""

    def __init__(self, name, description):
        self.name = name
        self.description = description
        self.properties = {}
        self.methods = {}

    def add_property(self, name, description, getter):
        self.properties[name] = Property(name, description, getter)

    def add_method(self, name, description, parameters, handler):
        self.methods[name] = {
            "description": description,
            "parameters": parameters,
            "handler": handler,
        }

    def get_descriptor(self):
        """生成小智协议的设备描述符"""
        return {
            "name": self.name,
            "description": self.description,
            "properties": {
                name: {"description": p.description, "type": "object"}
                for name, p in self.properties.items()
            },
            "methods": {
                name: {
                    "description": m["description"],
                    "parameters": [p.to_dict() for p in m["parameters"]],
                }
                for name, m in self.methods.items()
            },
        }

    def get_state(self):
        """生成当前状态快照"""
        return {name: p.get_value() for name, p in self.properties.items()}

    def invoke(self, method_name, arguments):
        """执行方法, 返回结果 dict"""
        method = self.methods.get(method_name)
        if method is None:
            return {"status": "error", "message": f"未知方法: {method_name}"}
        params = {}
        for p in method["parameters"]:
            value = arguments.get(p.name)
            if value is None and p.required:
                return {"status": "error", "message": f"缺少参数: {p.name}"}
            # MCP / IoT calls can run concurrently in executor threads.
            # Keep values local to the invocation, not on shared descriptors.
            local = Parameter(p.name, p.description, p.value_type, p.required)
            local.set_value(value)
            params[p.name] = local
        try:
            return method["handler"](params)
        except Exception as e:
            return {"status": "error", "message": f"执行失败: {e}"}


class ThingManager:
    """IoT 设备管理器 (单例)"""

    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = ThingManager()
        return cls._instance

    def __init__(self):
        self.things = {}

    def add_thing(self, thing):
        self.things[thing.name] = thing

    def get_descriptors(self):
        return [t.get_descriptor() for t in self.things.values()]

    def get_descriptors_json(self):
        return json.dumps(self.get_descriptors(), ensure_ascii=False)

    def get_states(self):
        return {name: t.get_state() for name, t in self.things.items()}

    def get_states_json(self):
        return json.dumps(self.get_states(), ensure_ascii=False)

    def invoke(self, command):
        """执行服务器下发的命令

        command 格式: {"name": "Thing.method", "method": "xxx", "properties": {...}}
        """
        full_name = command.get("name", "")
        method = command.get("method", "")
        arguments = command.get("properties", {}) or {}
        thing_name = full_name.split(".")[0] if "." in full_name else full_name
        thing = self.things.get(thing_name)
        if thing is None:
            # 只有一个设备时容忍名字不匹配
            if len(self.things) == 1:
                thing = next(iter(self.things.values()))
            else:
                return {"status": "error", "message": f"未知设备: {thing_name}"}
        return thing.invoke(method, arguments)
