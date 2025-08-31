# check_op_specs.py
import yaml
from yaml.constructor import ConstructorError
import os
from collections import Counter
import argparse

def check_for_duplicate_keys(loader, node):
    """A custom YAML constructor to check for duplicate keys."""
    keys = [loader.construct_object(key_node) for key_node, value_node in node.value]
    if len(keys) != len(set(keys)):
        duplicates = {key for key, count in Counter(keys).items() if count > 1}
        raise ConstructorError(
            "while constructing a mapping",
            node.start_mark,
            f"found duplicate key(s): {', '.join(map(repr, duplicates))}",
            node.start_mark,
        )
    return loader.construct_mapping(node)

# Apply the custom constructor to the SafeLoader
yaml.SafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, check_for_duplicate_keys
)

def validate_op_specs(file_path: str):
    """
    Loads and validates the op_specs.yaml file.
    Checks for:
    1. Basic YAML syntax errors.
    2. Invalid alias references (unresolved aliases).
    3. Duplicate keys within mappings.
    """
    print(f"--- Validating {file_path} ---")
    
    if not os.path.exists(file_path):
        print(f"❌ Error: File not found at '{file_path}'")
        return

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            # Using safe_load which now includes our duplicate key checker
            data = yaml.safe_load(f)
        
        print("✅ YAML syntax and alias references are valid.")
        print("✅ No duplicate keys found.")
        print("\nValidation successful: The file can be loaded without runtime errors.")

    except (yaml.YAMLError, ConstructorError) as e:
        print(f"❌ Validation FAILED. A runtime error occurred while parsing the file.")
        print("\n--- Error Details ---")
        if hasattr(e, 'problem_mark') and e.problem_mark:
            mark = e.problem_mark
            print(f"Error Type: {type(e).__name__}")
            print(f"Location:   Line {mark.line + 1}, Column {mark.column + 1}")
            print(f"Problem:    {e.problem}")
            if e.context:
                print(f"Context:    {e.context}")
        else:
            # For our custom ConstructorError or other errors without a mark
            print(f"Error: {e}")
        print("---------------------\n")
        print("Please fix the error(s) above to prevent runtime failures.")

if __name__ == "__main__":
    # 스크립트를 실행할 위치에 맞게 파일 경로를 수정하세요.
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=str, default='../op_specs.yaml', help="Path to the op_specs.yaml file")
    args = ap.parse_args()
    spec_file_path = args.file
    validate_op_specs(spec_file_path)
