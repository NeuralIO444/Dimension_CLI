import ast
import os

def check_file(path):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return
        
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if not node.body: continue
            
            # check for bare 'pass'
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                print(f"{path}:{node.lineno} - Empty function {node.name}")

if __name__ == "__main__":
    for root, dirs, files in os.walk("python"):
        for f in files:
            if f.endswith(".py"):
                check_file(os.path.join(root, f))
