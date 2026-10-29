"""Bounded picture extraction from the native Docling document tree."""
from io import BytesIO
from pathlib import Path

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_DOCUMENT_IMAGE_BYTES = 100 * 1024 * 1024
MAX_DOCUMENT_IMAGES = 200
IMAGE_MIMES = {'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp','GIF':'image/gif','BMP':'image/bmp','TIFF':'image/tiff'}


def inspect_image(data, mime_type=None, max_bytes=MAX_IMAGE_BYTES, max_pixels=MAX_IMAGE_PIXELS):
    from PIL import Image
    if not data or len(data)>max_bytes:
        raise ValueError('Image exceeds byte limit')
    try:
        with Image.open(BytesIO(data)) as image:
            actual=IMAGE_MIMES.get(image.format)
            if actual is None or (mime_type is not None and mime_type!=actual):
                raise ValueError('Unsupported or mismatched image MIME type')
            width,height=image.size
            if width<1 or height<1 or width*height>max_pixels:
                raise ValueError('Image exceeds pixel limit')
            image.verify()
    except Image.DecompressionBombError as exc:
        raise ValueError('Image exceeds pixel limit') from exc
    return dict(mime_type=actual,width=width,height=height)


def normalize_docling_document(document,*,scanned_pages=(),ocr_regions=(),digital_regions=()):
    """Replace picture payloads with relative URIs before exporting in tree order."""
    from docling_core.types.doc import PictureItem,TableItem, ImageRef, ImageRefMode, Size,ProvenanceItem,BoundingBox
    from PIL import Image
    # The conversion is owned by this parser. Avoid deep-copying all rendered
    # page pixels, which can double the memory needed for a large PDF.
    scanned_pages=set(scanned_pages)
    def index_regions(regions):
        indexed={}
        for identity,region in enumerate(regions):
            page=document.pages.get(region['page_no'])
            if page is None:continue
            box=BoundingBox.model_validate(region['bbox']).to_top_left_origin(page_height=page.size.height)
            indexed.setdefault(region['page_no'],[]).append((box,region['text'],identity))
        return indexed
    regions_by_page=index_regions(ocr_regions)
    digital_by_page=index_regions(digital_regions)
    def overlaps(left,right):
        return min(left.r,right.r)>max(left.l,right.l) and min(left.b,right.b)>max(left.t,right.t)
    raster_images=[];crop_counts={'table':0,'text':0}
    covering_pictures=[item for item,_ in document.iterate_items() if isinstance(item,PictureItem)]
    for source_item,_ in list(document.iterate_items()):
        if isinstance(source_item,PictureItem) or not (isinstance(source_item,TableItem) or hasattr(source_item,'text')):continue
        anchor=source_item
        empty_table=isinstance(source_item,TableItem) and not any(cell.text.strip() for cell in source_item.data.table_cells)
        for location in source_item.prov:
            page=document.pages.get(location.page_no)
            if page is None or location.page_no in scanned_pages:continue
            box=location.bbox.to_top_left_origin(page_height=page.size.height)
            ocr_boxes=[other for other,text,_ in regions_by_page.get(location.page_no,[]) if text.strip() and overlaps(box,other)]
            if not ocr_boxes and not empty_table:continue
            # A native picture already supplies these pixels to common OCR.
            def covered(region):
                for existing in covering_pictures:
                    for prov in existing.prov:
                        if prov.page_no!=location.page_no:continue
                        outer=prov.bbox.to_top_left_origin(page_height=page.size.height)
                        if outer.l-1<=region.l and outer.t-1<=region.t and outer.r+1>=region.r and outer.b+1>=region.b:return True
                return False
            if all(covered(region) for region in (ocr_boxes or [box])):continue
            if len(covering_pictures)>=MAX_DOCUMENT_IMAGES:raise ValueError('Document image count limit exceeded')
            kind='table' if isinstance(source_item,TableItem) else 'text'
            image=source_item.image if kind=='table' else None
            picture=document.insert_picture(sibling=anchor,image=image,prov=location,after=True)
            anchor=picture;covering_pictures.append(picture);crop_counts[kind]+=1
            raster_images.append((picture,kind,crop_counts[kind]))
    removed_ocr=[]
    for item,_ in document.iterate_items():
        if isinstance(item,PictureItem):continue
        targets=[item] if hasattr(item,'text') else list(getattr(getattr(item,'data',None),'table_cells',[]))
        for target in targets:
            original=target.text
            if target is item:
                cursor=0;inconsistent=False
                for start,end in sorted(location.charspan for location in item.prov):
                    if start<cursor or start<0 or end<start or end>len(original) or original[cursor:start].strip():inconsistent=True
                    cursor=max(cursor,end)
                if original[cursor:].strip():inconsistent=True
                if inconsistent:
                    native={};has_ocr=False
                    for location in item.prov:
                        page=document.pages.get(location.page_no)
                        if page is None:continue
                        box=location.bbox.to_top_left_origin(page_height=page.size.height)
                        has_ocr=has_ocr or any(text.strip() and overlaps(box,other) for other,text,_ in regions_by_page.get(location.page_no,[]))
                        native.update((identity,text) for other,text,identity in digital_by_page.get(location.page_no,[]) if text.strip() and overlaps(box,other))
                    if has_ocr:
                        target.text=' '.join(native[key] for key in sorted(native))
                        continue
            replacements=[]
            for location in getattr(item,'prov',[]):
                page=document.pages.get(location.page_no)
                target_box=location.bbox if target is item else target.bbox
                if page is None or target_box is None:continue
                box=target_box.to_top_left_origin(page_height=page.size.height)
                start,end=location.charspan if target is item else (0,len(original))
                start,end=max(0,start),min(len(original),end)
                if end<=start:continue
                if not any(text.strip() and overlaps(box,other) for other,text,_ in regions_by_page.get(location.page_no,[])):continue
                # Geometry and native PDF provenance own attribution. Native OCR
                # normalizes quotes and dehyphenates, so textual equality cannot
                # reliably separate it. Rebuild an ambiguous span only from its
                # actual overlapping digital cells; OCR-only spans become empty.
                native=[(identity,text) for other,text,identity in digital_by_page.get(location.page_no,[]) if text.strip() and overlaps(box,other)]
                replacements.append((start,end,native))
            merged=[]
            for start,end,native in sorted(replacements):
                if merged and start<merged[-1][1]:
                    previous=merged[-1];merged[-1]=(previous[0],max(end,previous[1]),previous[2]+native)
                else:merged.append((start,end,native))
            for start,end,native in reversed(merged):
                unique=dict(native)
                replacement=' '.join(unique[key] for key in sorted(unique))
                target.text=target.text[:start]+replacement+target.text[end:]
            if replacements:target.text=target.text.strip()
        if hasattr(item,'text') and not item.text.strip():removed_ocr.append(item)
        elif isinstance(item,TableItem) and not any(cell.text.strip() for cell in item.data.table_cells):removed_ocr.append(item)
    if removed_ocr:document.delete_items(node_items=removed_ocr)
    if scanned_pages:
        # Native PDF OCR has no independent quality receipt. Preserve the scan
        # itself and let the common image OCR gate own searchable scan text.
        removed=[item for item,_ in document.iterate_items() if getattr(item,'prov',[]) and
                 {p.page_no for p in item.prov}.issubset(scanned_pages)]
        if removed:document.delete_items(node_items=removed)
    fallback_items=[]
    for page_no in sorted(scanned_pages):
        page=document.pages.get(page_no)
        if page is None:continue
        prov=ProvenanceItem(page_no=page_no,bbox=BoundingBox(l=0,t=0,r=page.size.width,b=page.size.height),charspan=(0,0))
        sibling=next((item for item,_ in document.iterate_items() if any(p.page_no>=page_no for p in getattr(item,'prov',[]))),None)
        picture=document.insert_picture(sibling=sibling,image=page.image,prov=prov,after=False) if sibling is not None else document.add_picture(image=page.image,prov=prov)
        fallback_items.append((picture,page_no))
    fallback_pages={picture.self_ref:page_no for picture,page_no in fallback_items}
    raster_paths={picture.self_ref:(kind,number) for picture,kind,number in raster_images}
    assets=[];items=[];warnings=[];total=0
    for ordinal,(item,level) in enumerate(document.iterate_items()):
        provenance=[p.model_dump(mode='json') for p in getattr(item,'prov',[])]
        entry=dict(ordinal=ordinal,self_ref=item.self_ref,label=item.label.value,provenance=provenance)
        if isinstance(item,PictureItem):
            page_no=fallback_pages.get(item.self_ref)
            raster=raster_paths.get(item.self_ref)
            path=f'assets/page-{page_no}.png' if page_no is not None else f'assets/{raster[0]}-{raster[1]}.png' if raster is not None else f'assets/picture-{len(assets)+1}.png'
            caption=item.caption_text(document)
            asset=dict(kind='local',status='missing',original_path=path,path=path,alt=caption,
                       provenance=dict(self_ref=item.self_ref,ordinal=ordinal,caption=caption,locations=provenance))
            if page_no is not None:asset['provenance'].update(kind='scanned_page',page_no=page_no)
            if raster is not None:asset['provenance'].update(kind=raster[0]+'_image')
            try:
                if len(assets)>=MAX_DOCUMENT_IMAGES: raise ValueError('Document image count limit exceeded')
                image=item.get_image(document)
                if image is None: raise ValueError('Picture pixels are unavailable')
                if image.width*image.height>MAX_IMAGE_PIXELS: raise ValueError('Image exceeds pixel limit')
                buffer=BytesIO();image.save(buffer,format='PNG');data=buffer.getvalue()
                details=inspect_image(data)
                total+=len(data)
                if total>MAX_DOCUMENT_IMAGE_BYTES: raise ValueError('Document image byte limit exceeded')
                asset.update(details,data=data,status='available')
            except (ValueError,OSError,Image.DecompressionBombError) as exc:
                warnings.append(f'{path}: {exc}')
            # Keep a referenced item even when extraction fails: a visible missing image.
            item.image=ImageRef(mimetype='image/png',dpi=72,size=Size(width=asset.get('width',1),height=asset.get('height',1)),uri=Path(path))
            entry['image_path']=path
            assets.append(asset)
        items.append(entry)
        if len(items)>20_000: raise ValueError('Document structure exceeds item limit')
    text=document.export_to_markdown(image_mode=ImageRefMode.REFERENCED)
    for asset in assets:
        if not asset['alt']:
            text=text.replace('![Image]('+asset['path']+')','![]('+asset['path']+')')
    return text,assets,dict(items=items,scanned_pages=sorted(scanned_pages)),warnings
