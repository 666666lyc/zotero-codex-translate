"""Real BabelDOC/PDF renderer smoke test with a deterministic fake translator.

Downloads verified layout/font assets on first use. This tests plumbing and
layout, not Codex translation quality. Output is confined to tmp/.
"""
import pathlib
import re
import sys

def main():
    import faulthandler
    faulthandler.dump_traceback_later(90, repeat=True)
    root = pathlib.Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "bridge" / "src"))
    from zotero_codex_bridge.pdf_engine import run_translation
    from zotero_codex_bridge.storage import Store, fingerprint
    from zotero_codex_bridge.translation import TranslationService, TOKENS
    import pymupdf

    directory = root / "tmp" / "pdf-pipeline"
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "fixture.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((45, 55), "A scientific translation fixture", fontsize=18)
    for x in (45, 310):
        page.insert_textbox((x, 90, x + 230, 350), "This is an English scientific paragraph. We evaluate a method and report its results. The equation uses a parameter x and a measured value y.\n\nA second paragraph discusses limitations and future work. Citations [1] and [2] remain unchanged.", fontsize=11)
    page.draw_rect((60, 390, 500, 500), color=(0.2, 0.4, 0.6))
    page.insert_text((60, 530), "Figure 1. Experimental diagram.", fontsize=11)
    page = document.new_page()
    page.insert_text((45, 55), "References", fontsize=18)
    page.insert_textbox((45, 100, 520, 300), "[1] Smith J. An example scientific article. Journal of Testing, 2025.\n[2] Li X. A second paper. Proceedings of Example Conference, 2024.", fontsize=11)
    document.save(source)
    document.close()

    class FakeTranslator:
        def translate(self, text, model):
            # Deliberately short, deterministic Chinese to isolate PDF layout.
            if not re.search(r"[A-Za-z]", text):
                return text
            return "这是用于检查中文排版和文档完整性的测试译文。" + " ".join(TOKENS.findall(text))
        def close(self):
            pass

    store = Store(directory / "store")
    service = TranslationService(store, FakeTranslator, fingerprint(source), "fake-layout-test")
    last_stage = ""
    def notify(event):
        nonlocal last_stage
        if event["stage"] != last_stage:
            print(event["stage"], flush=True)
            last_stage = event["stage"]
    try:
        result = run_translation(source, directory / "translated.pdf", directory / "work", service, notify)
        with pymupdf.open(directory / "translated.pdf") as pdf:
            assert pdf.page_count == 2
            assert "测试译文" in pdf[0].get_text()
            assert "Smith J." in pdf[1].get_text()
            assert "测试译文" not in pdf[1].get_text()
            for index, page in enumerate(pdf):
                page.get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3)).save(directory / f"page-{index+1}.png")
        print(result)
    finally:
        service.close()
        store.close()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
