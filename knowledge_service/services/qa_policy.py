"""Conservative QA purpose and original-source quotation authorization."""
import re

DOCUMENT = 'document_explanation'
RULE = 'rule_judgment'
DOCUMENT_LABEL = '按文档说明，有效性待核对'


def purpose(request):
    # Never classify using the implicit clock added by freeze_request, or trust
    # an API caller's answer_purpose value to relax rule eligibility.
    if request.get('_explicit_valid_at', request.get('valid_at') is not None):
        return RULE
    query = str(request.get('query') or '').strip().casefold()
    if re.search(r'当前|现在|现行|生效|有效|失效|适用|能否|可否|是否|可以|能不能|应当|应该|必须|不得|禁止|允许|许可|合规|执行|需要|要求|条件|能用|可用|截至|截止|今天|明天|昨天|当时|时点|时候|怎么办|如何办理|怎么操作|如何操作|\d{4}(?:年|[-/]\d)|\b(current|effective|applicab\w*|valid|allowed|must|should|can|may)\b', query):
        return RULE
    if re.search(r'流程|步骤|操作|办理|怎么|如何|\b(how|procedures?|steps?|requirements?|regulations?)\b', query):
        return RULE
    if (re.search(r'规则|规定|\b(rules?|polic(?:y|ies))\b', query)
            and not re.search(r'是什么|什么是|定义|含义|\b(what is|define|definition)\b', query)):
        return RULE
    if re.search(r'是什么|什么是|定义|含义|解释|介绍|概述|说明|种类|类型|有哪些|分为|\b(what is|what are|define|definition|explain|types of)\b', query):
        return DOCUMENT
    # A short noun topic is descriptive; ambiguous questions stay conservative.
    if (0 < len(query) <= 40 and re.fullmatch(r'[\w\s·、（）()/-]+', query)
            and re.search(r'价格|价钱|费用|术语|概念|定义|产品|服务|分类|类型', query)
            and not re.search(r'怎么|如何|为何|为什么|多少|几|吗|么|哪天|何时|条件|规则|要求|流程', query)):
        return DOCUMENT
    return RULE


def original_quote_allowed(row):
    """Only exact authorized original chunks, never derived graph statements."""
    status = row.get('validity_status')
    expected = {'unknown': ('U', 'qualified_quote'),
                'expired': ('R', 'reference'),
                'not_yet_effective': ('F', 'reference')}.get(status)
    if not expected or row.get('kind') != 'chunk' or row.get('answer_eligible') is not False:
        return False
    prefix, usage = expected
    version = (row.get('metadata') or {}).get('source_version_id')
    return (str(row.get('citation', '')).startswith(prefix) and row.get('usage') == usage
            and bool(version) and version in row.get('_allowed_source_version_ids', [])
            and bool(row.get('version_id'))
            and row['version_id'] in row.get('_allowed_chunk_version_ids', []))


def model_evidence(evidence, context):
    rows = list(evidence)
    if context.get('answer_purpose') == DOCUMENT:
        allowed = set(context.get('_document_quote_citations', []))
        rows.extend(row for key in ('uncertain_references', 'historical_references', 'future_references')
                    for row in context.get(key, []) if row['citation'] in allowed)
    return rows
