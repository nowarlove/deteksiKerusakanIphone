"""Menjalankan cell kode pso.ipynb secara reproducible tanpa Jupyter."""

import json
import os

os.environ.setdefault("MPLBACKEND", "Agg")


def main():
    with open("pso.ipynb", "r", encoding="utf-8") as source:
        notebook = json.load(source)
    namespace = {"__name__": "__pso__"}
    for index, cell in enumerate(notebook.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        code = "".join(cell.get("source", []))
        print(f"Menjalankan cell {index}...")
        exec(compile(code, f"pso.ipynb:cell-{index}", "exec"), namespace)


if __name__ == "__main__":
    main()
