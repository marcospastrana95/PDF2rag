import pymupdf
import pymupdf4llm
from pathlib import Path
import re

pdf_path = Path(r"c:\Users\marco\Desktop\desarrollo IA\script conversion pdf a md\pdfs\Cuestiones sobre la cronología de la Cueva Pintada de Gáldar (Gran Canaria).pdf")

doc = pymupdf.open(pdf_path)
total_words = 0
for i, page in enumerate(doc):
    text = page.get_text()
    words = len(re.findall(r"\b\w+\b", text))
    print(f"Página {i+1}: {words} palabras")
    total_words += words
    if words > 0:
        print(f"  Muestra: {text[:50]!r}")
doc.close()

print(f"\nTotal palabras (nativa): {total_words}")

md_text = pymupdf4llm.to_markdown(str(pdf_path))
md_words = len(re.findall(r"\b\w+\b", md_text))
print(f"Total palabras (markdown): {md_words}")
