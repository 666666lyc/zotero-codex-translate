"""Synthetic 12-page layout matrix using the real pinned engine, no account."""
import hashlib
import io
import json
from pathlib import Path
import re
import sys


def main():
    import pymupdf
    from zotero_codex_bridge.pdf_engine import run_translation
    from zotero_codex_bridge.storage import Store, fingerprint
    from zotero_codex_bridge.translation import TranslationService, TOKENS
    directory = Path(__file__).resolve().parents[1] / "tmp/pdf-matrix/中文路径"
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "英文测试文献.pdf"
    pdf = pymupdf.open()
    body = "The scientific experiment evaluates a proposed method with a measured parameter. We discuss the observations, numerical results and limitations. Citation [1] identifies the original method."
    for index in range(12):
        page = pdf.new_page()
        page.insert_text((45, 55), f"Scientific layout fixture {index + 1}", fontsize=17)
        if index == 1:
            for x in (45, 310):
                page.insert_textbox((x, 90, x + 230, 330), body + "\n\n" + body, fontsize=11)
        else:
            page.insert_textbox((45, 90, 550, 250), body + "\n\n" + body, fontsize=11)
        if index == 2:
            page.insert_text((100, 320), "x = y + 1", fontsize=20, fontname="tiit")
            # Embed a raster diagram. The renderer must preserve the pixels.
            drawing = pymupdf.open()
            figure = drawing.new_page(width=300, height=100)
            figure.draw_rect((5, 5, 295, 95), color=(.2, .4, .6))
            figure.insert_text((20, 50), "IMAGE TEXT STAYS", fontsize=16)
            png = figure.get_pixmap().tobytes("png")
            drawing.close()
            page.insert_image((70, 410, 520, 560), stream=png)
            page.insert_text((70, 590), "Figure 1. A raster diagram with unmodified internal text.", fontsize=11)
        if index == 3:
            for row in range(5):
                y = 310 + row * 35
                page.draw_line((60, y), (520, y))
                if row < 4:
                    page.insert_text((80, y + 23), f"Sample {row + 1}", fontsize=11)
                    page.insert_text((300, y + 23), str(100 + row), fontsize=11)
            for x in (60, 280, 520):
                page.draw_line((x, 310), (x, 450))
            page.insert_text((60, 480), "Table 1. Experimental measurements.", fontsize=11)
        if index == 11:
            page.insert_text((45, 350), "References", fontsize=17)
            page.insert_text((45, 390), "[1] Smith J. Scientific test. Journal of Testing, 2025.", fontsize=11)
    pdf.save(source)
    pdf.close()

    class Translator:
        def translate(self, text, model):
            with (directory / "synthetic-translator-inputs.jsonl").open("a", encoding="utf-8") as trace:
                trace.write(json.dumps(text, ensure_ascii=False) + "\n")
            if not re.search(r"[A-Za-z]", text):
                return text
            content = TOKENS.sub("", text).strip()
            if re.fullmatch(r"[A-Za-z]{1,2}", content):
                return text
            if TOKENS.search(text) and re.fullmatch(r"(?:[A-Za-z]\s*){1,4}", content):
                return text
            if re.search(r"[=+*/^]", content) and re.fullmatch(r"[A-Za-z0-9+*/=^().\s-]+", content):
                return text
            short = len(re.findall(r"[A-Za-z]+", text)) < 10
            return ("测试译文" if short else "这是用于检验长文、中文字体与内容完整性的测试译文。") + " ".join(TOKENS.findall(text) + re.findall(r"\b\d+\b", text))
        def close(self):
            pass

    store = Store(directory / "store")
    service = TranslationService(store, Translator, fingerprint(source), "fake-layout-matrix-v5")
    report = {"passed": False, "checks": []}
    output = directory / "中文译文.pdf"
    try:
        result = run_translation(source, output, directory / "work", service, lambda event: None)
        with pymupdf.open(source) as original, pymupdf.open(output) as target:
            assert target.page_count == 12
            assert all("测试译文" in page.get_text() for page in target)
            report["checks"].extend(["12-page-correspondence-and-every-page-text", "single-and-double-column-layout"])
            assert "Smith J." in target[-1].get_text()
            report["checks"].append("bibliography-preserved")
            image_hash = lambda doc, xref: hashlib.sha256(pymupdf.Pixmap(doc, xref).samples).hexdigest()
            source_images = {image_hash(original, image[0]) for image in original[2].get_images()}
            target_images = {image_hash(target, image[0]) for image in target[2].get_images()}
            assert source_images and source_images.issubset(target_images)
            report["checks"].append("raster-image-pixels-preserved")
            assert all(str(number) in target[3].get_text() for number in range(100, 104))
            report["checks"].append("table-numeric-values-preserved")
            formula_text = target[2].get_text()
            assert all(token in formula_text for token in ("x", "y", "+", "1")), formula_text
            report["checks"].append("simple-isolated-formula-preserved")
            for index in (0, 1, 2, 3, 11):
                target[index].get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3)).save(directory / f"page-{index + 1}.png")
        report.update(passed=True, result=result)
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        service.close()
        store.close()
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
