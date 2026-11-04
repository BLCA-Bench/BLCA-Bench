import sys
import os
import json
from pathlib import Path
from pdf_extract_and_generate import process_pdf_and_generate_questions, extract_pdf_text, chunk_text

def process_single(pdf_path, category=None, question_count=100):
    """处理单个PDF文件并生成问题"""
    if not os.path.exists(pdf_path):
        print(f"错误: 文件 {pdf_path} 不存在")
        return
    
    if category is None:
        # 尝试从路径推断分类
        path = Path(pdf_path)
        parent_dir = path.parent.name
        if any(name in parent_dir for name in ["流行病学CA2025", "AUA", "EAU", "NCCN"]):
            category = parent_dir
        else:
            category = "其他指南"
    
    process_pdf_and_generate_questions(pdf_path, category, total_questions=question_count)

def list_available_guides():
    """列出所有可用的指南文件"""
    guides_path = Path("data/guides")
    print("=== 可用的指南文件 ===")
    
    # 流行病学CA2025
    print("\n流行病学CA2025:")
    ca2025_path = guides_path / "流行病学CA2025"
    if ca2025_path.exists():
        for i, pdf_file in enumerate(ca2025_path.glob("*.pdf")):
            print(f"  {i+1}. {pdf_file.name}")
    else:
        print("  目录不存在")
    
    # AUA
    print("\nAUA:")
    aua_path = guides_path / "AUA"
    if aua_path.exists():
        for i, pdf_file in enumerate(aua_path.glob("*.pdf")):
            print(f"  {i+1}. {pdf_file.name}")
    else:
        print("  目录不存在")
    
    # EAU
    print("\nEAU:")
    eau_path = guides_path / "EAU"
    if eau_path.exists():
        for i, pdf_file in enumerate(eau_path.glob("*.pdf")):
            print(f"  {i+1}. {pdf_file.name}")
    else:
        print("  目录不存在")
    
    # NCCN
    print("\nNCCN:")
    nccn_path = guides_path / "NCCN"
    if nccn_path.exists():
        for i, pdf_file in enumerate(nccn_path.glob("*.pdf")):
            print(f"  {i+1}. {pdf_file.name}")
    else:
        print("  目录不存在")

def list_generated_files():
    """列出所有已生成的问题文件"""
    output_path = Path("output")
    if not output_path.exists():
        print("错误: 输出目录不存在")
        return
        
    print("=== 已生成的问题文件 ===")
    json_files = list(output_path.glob("*.json"))
    
    if not json_files:
        print("未找到已生成的问题文件")
        return
        
    for i, json_file in enumerate(json_files):
        print(f"  {i+1}. {json_file.name}")

def view_json_questions(json_file):
    """查看JSON格式的问题文件"""
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            questions = json.load(f)
            
        print(f"\n=== {json_file} 包含 {len(questions)} 个问题 ===\n")
        
        # 显示前3个问题作为示例
        sample_count = min(3, len(questions))
        for i in range(sample_count):
            q = questions[i]
            print(f"问题 {i+1}: {q['question']}")
            for option in q['options']:
                print(f"  {option}")
            print(f"正确答案: {q['answer']}")
            if 'explanation' in q:
                print(f"解释: {q['explanation']}")
            print()
            
        print(f"... 共 {len(questions)} 个问题")
    except FileNotFoundError:
        print(f"错误: 文件 {json_file} 不存在")
    except json.JSONDecodeError:
        print(f"错误: 文件 {json_file} 不是有效的JSON格式")
    except Exception as e:
        print(f"读取JSON文件时出错: {str(e)}")
        import traceback
        traceback.print_exc()

def main():
    if len(sys.argv) < 2:
        print("用法: python process_single_guide.py <PDF文件路径> [分类] [问题数量]")
        print("或使用: python process_single_guide.py --list 列出所有可用指南")
        print("或使用: python process_single_guide.py --output 列出所有已生成的问题文件")
        print("或使用: python process_single_guide.py --view <JSON文件路径> 查看生成的问题")
        return
    
    if sys.argv[1] == "--list":
        list_available_guides()
        return
        
    if sys.argv[1] == "--output":
        list_generated_files()
        return
    
    if sys.argv[1] == "--view":
        if len(sys.argv) < 3:
            print("错误: 请提供JSON文件路径")
            return
        view_json_questions(sys.argv[2])
        return
    
    pdf_path = sys.argv[1]
    category = sys.argv[2] if len(sys.argv) > 2 else None
    question_count = int(sys.argv[3]) if len(sys.argv) > 3 else 100
    
    process_single(pdf_path, category, question_count)

if __name__ == "__main__":
    main()
