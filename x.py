from pathlib import Path

root = Path(__file__).resolve().parent
for path in sorted(root.iterdir()):
    print(path.name)
