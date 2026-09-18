"""对审核的属性值进行严格校验，且不改变旧数据导入行为。"""
import math
from rdflib import Literal, RDFS
from rdflib.namespace import XSD


def validate_attribute(model,entity_type,attribute,value):
    term=model.resolve(entity_type,model.classes)
    predicate=model.resolve(attribute,model.attributes)
    if type(value) not in (str,int,float,bool) or isinstance(value,float) and not math.isfinite(value):
        raise ValueError('属性值必须为有限数值、字符串或布尔值')
    for domain in model.graph.objects(predicate,RDFS.domain):
        if domain not in model.parents(term):raise ValueError('属性 domain 与实体类型不匹配')
    for datatype in model.graph.objects(predicate,RDFS.range):
        if datatype==RDFS.Literal:continue
        literal=Literal(value)
        if literal.datatype!=datatype and not (datatype==XSD.string and isinstance(value,str)):
            raise ValueError(f'属性值数据类型不匹配：需要 {datatype}，实际 {literal.datatype}')
