"""Download Docling PDF models before deployment; requests only use local files."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'data/model/document-parser/docling')
    parser.add_argument('--check',action='store_true',help='Validate prepared files without downloading')
    args=parser.parse_args(argv)
    root=args.output_dir.expanduser().resolve()
    if not args.check:
        from docling.utils.model_downloader import download_models
        root.mkdir(parents=True,exist_ok=True)
        download_models(output_dir=root,progress=True,with_layout=True,with_tableformer=True,
            with_tableformer_v2=False,with_code_formula=False,with_picture_classifier=False,
            with_rapidocr=True,with_easyocr=False)
    sys.path.insert(0,str(ROOT))
    import os
    os.environ['KG_DOCUMENT_ARTIFACTS_PATH']=str(root)
    from knowledge_service.integrations.document_parser import document_artifacts_path
    document_artifacts_path(require_ocr=True)
    print('Docling PDF models are ready: '+str(root))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
