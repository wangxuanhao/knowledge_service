"""Shared primitive attribute datatype policy."""
import math


XSD_NAMESPACE = 'http://www.w3.org/2001/XMLSchema#'


def primitive_datatype(value: str | bool | int | float) -> str:
    """Return the one canonical XSD datatype for a supported native primitive."""
    if type(value) is str:
        return XSD_NAMESPACE + 'string'
    if type(value) is bool:
        return XSD_NAMESPACE + 'boolean'
    if type(value) is int:
        return XSD_NAMESPACE + 'integer'
    if type(value) is float and math.isfinite(value):
        return XSD_NAMESPACE + 'double'
    raise ValueError('属性值必须是字符串、布尔值、整数或有限浮点数')
