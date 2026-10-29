"""Small real image payload for source/media contracts that validate raster bytes."""
import io


def png():
    from PIL import Image
    stream=io.BytesIO()
    Image.new('RGB',(8,8),'red').save(stream,format='PNG')
    return stream.getvalue()
