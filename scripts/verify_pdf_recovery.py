"""Local rendered-image, CJK-font and page correspondence acceptance checks."""
import argparse
import json
from pathlib import Path
import pymupdf

parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("target", type=Path)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
report = {"passed": False, "pages": [], "imagesChecked": 0}
try:
    with pymupdf.open(args.source) as source, pymupdf.open(args.target) as target:
        assert len(source) == len(target)
        for index, (original, translated) in enumerate(zip(source, target)):
            assert tuple(original.rect) == tuple(translated.rect)
            sizes = [s["size"] for b in translated.get_text("dict")["blocks"] for l in b.get("lines",[]) for s in l["spans"] if any("\u3400" <= c <= "\u9fff" for c in s["text"])]
            assert not sizes or min(sizes) >= 6.95, (index + 1, "font")
            differences = []
            for image in original.get_images(full=True):
                for rect in original.get_image_rects(image[0]):
                    if rect.width < 3 or rect.height < 3:
                        continue
                    a = original.get_pixmap(clip=rect, alpha=False)
                    b = translated.get_pixmap(clip=rect, alpha=False)
                    assert (a.width, a.height, a.n) == (b.width, b.height, b.n)
                    delta = sum(abs(x-y) for x,y in zip(a.samples,b.samples))/(255*len(a.samples))
                    differences.append(round(delta, 6))
                    assert delta < .025, (index + 1, "image changed", delta)
                    report["imagesChecked"] += 1
            report["pages"].append({"page":index + 1,"minimumCjkFontSize":min(sizes) if sizes else None,"renderedImageDifferences":differences})
        report["passed"] = True
except Exception as error:
    report["error"] = str(error)
args.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
print(json.dumps(report),flush=True)
raise SystemExit(0 if report["passed"] else 1)
