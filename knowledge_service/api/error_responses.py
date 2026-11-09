from fastapi.responses import JSONResponse


def database_unavailable_response():
    return JSONResponse(status_code=503, content={
        'code': 'database_unavailable',
        'detail': '数据库连接暂时不可用，请稍后重试。',
    })
