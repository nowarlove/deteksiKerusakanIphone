"""Menjalankan cell kode preprocessing.ipynb secara reproducible tanpa Jupyter."""

import json
import os

os.environ.setdefault("MPLBACKEND", "Agg")


def main():
    with open("preprocessing.ipynb", "r", encoding="utf-8") as source:
        notebook = json.load(source)
    namespace = {"__name__": "__preprocessing__"}
    for index, cell in enumerate(notebook.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        code = "".join(cell.get("source", []))
        code = code.replace("from IPython.core import release\n", "")
        print(f"Menjalankan cell {index}...")
        exec(compile(code, f"preprocessing.ipynb:cell-{index}", "exec"), namespace)


if __name__ == "__main__":
    main()
