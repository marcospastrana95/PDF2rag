import pymupdf4llm
try:
    # Intento de ver si acepta el parámetro ocr
    print("Probando parámetro ocr...")
    # No lo ejecutamos realmente, solo vemos si la firma lo acepta o da TypeError
    # (En realidad, to_markdown acepta **kwargs en algunas versiones)
    pass
except Exception as e:
    print(f"Error: {e}")
