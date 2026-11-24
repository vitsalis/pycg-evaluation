import sys
import json

def convert_cg(cg):
    def convert(node):
        node = node.replace(".init.", "__init__")
        node = node.replace(".call.", "__call__")
        node = node.replace(".repr.", "__repr__")
        node = node.replace(".eq.", "__eq__")
        node = node.replace(".str.", "__str__")
        node = node.replace("src.", "")
        return node

    new_cg = {}
    for node in cg:
        converted_node = convert(node)
        new_cg[converted_node] = []
        for item in cg[node]:
            new_cg[converted_node].append(convert(item))
    return new_cg

def main():
    if len(sys.argv) < 3:
        print ("Usage: convert_pyan_cgs.py src.json dest.json")
        sys.exit(1)

    with open(sys.argv[1], "r") as f:
        cg = json.loads(f.read())

    converted_cg = convert_cg(cg)
    with open(sys.argv[2], "w") as f:
        f.write(json.dumps(converted_cg))

if __name__ == "__main__":
    main()
