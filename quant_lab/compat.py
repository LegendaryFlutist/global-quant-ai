"""旧导入路径兼容层；模块对象复用可保持旧补丁和模型反序列化行为。"""
from importlib import import_module
import sys

def alias_legacy(legacy_name, module_name):
    """将旧模块名绑定到同一个实际模块，保留私有函数、测试补丁及旧pickle路径。

    参数：legacy_name为旧导入名，module_name为分包后的完整模块名。
    返回：实际模块对象；首次导入时可能传播目标模块的ImportError。
    此函数只建立导入别名，不复制业务实现，也不执行训练、下单或通知。
    """
    module = import_module(module_name)
    sys.modules[legacy_name] = module
    return module
