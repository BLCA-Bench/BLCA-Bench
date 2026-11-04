import os
import PyPDF2
import pdfplumber
import requests
import json
import re
from pathlib import Path
import time
import traceback
import logging

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("pdf_process.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# API配置（凭证在运行时从环境变量读取）
model_name = os.getenv("BLCA_MODEL", "gpt-4o")
api_key = os.getenv("BLCA_API_KEY", "")
api_url = os.getenv("BLCA_API_URL", "")

# 指南路径
guides_path = Path("data/guides")
output_path = Path("output")

# 确保输出目录存在
os.makedirs(output_path, exist_ok=True)

# 提取PDF内容的函数 - 首先尝试PyPDF2，如果失败则尝试pdfplumber
def extract_pdf_text(pdf_path):
    text = ""
    
    # 首先尝试使用PyPDF2
    try:
        logger.info(f"使用PyPDF2提取 {pdf_path}")
        with open(pdf_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            total_pages = len(reader.pages)
            logger.info(f"总页数: {total_pages}")
            
            for i, page in enumerate(reader.pages):
                try:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                    if (i+1) % 10 == 0:
                        logger.info(f"已处理 {i+1}/{total_pages} 页")
                except Exception as e:
                    logger.warning(f"PyPDF2无法提取第 {i+1} 页: {str(e)}")
        
        # 检查是否成功提取了文本
        if len(text.strip()) < 100:  # 如果提取的文本太少
            logger.warning(f"PyPDF2提取的文本太少（{len(text)}字符），尝试使用pdfplumber")
            text = ""  # 重置文本
        else:
            logger.info(f"使用PyPDF2提取完成，总长度: {len(text)} 字符")
            return text
            
    except Exception as e:
        logger.warning(f"PyPDF2提取失败: {str(e)}")
        text = ""  # 重置文本
    
    # 如果PyPDF2失败，尝试使用pdfplumber
    if not text:
        try:
            logger.info(f"使用pdfplumber提取 {pdf_path}")
            with pdfplumber.open(pdf_path) as pdf:
                total_pages = len(pdf.pages)
                logger.info(f"总页数: {total_pages}")
                
                for i, page in enumerate(pdf.pages):
                    try:
                        page_text = page.extract_text()
                        if page_text:
                            text += page_text + "\n"
                        if (i+1) % 10 == 0:
                            logger.info(f"已处理 {i+1}/{total_pages} 页")
                    except Exception as e:
                        logger.warning(f"pdfplumber无法提取第 {i+1} 页: {str(e)}")
            
            logger.info(f"使用pdfplumber提取完成，总长度: {len(text)} 字符")
        except Exception as e:
            logger.error(f"pdfplumber提取失败: {str(e)}")
            logger.error(traceback.format_exc())
    
    if not text:
        logger.error(f"所有PDF提取方法都失败，无法从 {pdf_path} 提取文本")
        
    return text

# 将长文本分割成小块
def chunk_text(text, chunk_size=8000):
    try:
        words = text.split()
        chunks = []
        current_chunk = []
        current_size = 0
        
        for word in words:
            current_chunk.append(word)
            current_size += len(word) + 1  # +1 for the space
            
            if current_size >= chunk_size:
                chunks.append(' '.join(current_chunk))
                current_chunk = []
                current_size = 0
        
        if current_chunk:
            chunks.append(' '.join(current_chunk))
        
        logger.info(f"文本被分割成 {len(chunks)} 个块")
        return chunks
    except Exception as e:
        logger.error(f"分块文本时出错: {str(e)}")
        logger.error(traceback.format_exc())
        return []

# 使用GPT API生成问题
def generate_questions(text_chunk, category, guide_name, question_count=10, max_retries=3, specific_category=None):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    category_instruction = ""
    if specific_category:
        category_instruction = f"请只生成{specific_category}类的问题。"
    
    prompt = f"""
    我需要您基于以下医学指南内容，生成{question_count}道关于膀胱癌的选择题。
    每个问题应该有5个选项(A-E)，并标明正确答案。
    
    请将问题分为以下6个类别，并在每个问题中标明类别：
    1. 流行病学：关于膀胱癌的流行情况、发病率、死亡率、地区分布等
    2. 病因学：关于膀胱癌的病因、危险因素、诱因等
    3. 病理学：关于膀胱癌的病理类型、分级分期、组织学特征等
    4. 治疗：关于膀胱癌的治疗方法、手术方式、药物治疗等
    5. 预防：关于膀胱癌的预防、筛查、早期发现等
    6. 诊断：关于膀胱癌的诊断方法、影像学特征、实验室检查等
    
    {category_instruction}
    
    请直接以JSON格式输出，格式如下:
    [
      {{
        "question": "问题描述？",
        "options": [
          "A. 选项A",
          "B. 选项B", 
          "C. 选项C",
          "D. 选项D",
          "E. 选项E"
        ],
        "answer": "C",  // 正确答案的选项字母(A,B,C,D,E)
        "explanation": "解释为什么这是正确答案",
        "category": "流行病学"  // 必须是以下之一：流行病学、病因学、病理学、治疗、预防、诊断
      }},
      ...更多问题
    ]

    以下是医学指南文本：
    {text_chunk}
    
    请确保问题是有意义的，选项合理，并且答案确实可以从指南内容中找到依据。
    必须提取膀胱癌相关的内容。
    必须严格按照指定的JSON格式输出，不要包含任何其他格式或额外文本。
    特别注意：
    1. answer必须是选项字母(A,B,C,D,E)，而不是数字索引
    2. options数组中的每个选项必须包含选项字母前缀，如"A. 选项内容"
    3. category必须是指定的6个类别之一
    """
    
    data = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "你是一位医学教育专家，擅长根据医学指南创建高质量的选择题。你特别熟悉膀胱癌相关的临床知识。你的输出必须是可解析的JSON格式。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7
    }
    
    for attempt in range(max_retries):
        try:
            logger.info(f"发送API请求 (尝试 {attempt+1}/{max_retries})...")
            response = requests.post(api_url, headers=headers, data=json.dumps(data))
            
            if response.status_code == 200:
                result = response.json()
                logger.debug(f"API响应: {str(result)}")
                
                # 检查API响应格式
                if "choices" in result:
                    # OpenAI格式响应
                    content = result["choices"][0]["message"]["content"]
                elif "message" in result:
                    # 服务商专有格式响应
                    content = result["message"]["content"]
                else:
                    # 未知格式，尝试打印响应内容
                    logger.error(f"未知的API响应格式: {str(result)}")
                    content = str(result)
                
                logger.info(f"成功生成问题，长度: {len(content)} 字符")
                
                # 尝试提取JSON内容
                try:
                    # 使用正则表达式提取JSON部分
                    json_match = re.search(r'(\[[\s\S]*\])', content)
                    if json_match:
                        json_str = json_match.group(1)
                        # 尝试解析JSON以验证格式
                        questions_json = json.loads(json_str)
                        
                        # 确保answer是正确的字母格式
                        for q in questions_json:
                            if 'answer' in q:
                                # 如果answer是数字，转换为字母
                                if isinstance(q['answer'], int) or (isinstance(q['answer'], str) and q['answer'].isdigit()):
                                    try:
                                        index = int(q['answer'])
                                        q['answer'] = chr(65 + index)  # 0->A, 1->B, etc.
                                    except ValueError:
                                        logger.warning(f"无法将answer值 '{q['answer']}' 转换为字母，保持原样")
                                
                                # 确保answer是大写字母
                                if isinstance(q['answer'], str) and len(q['answer']) == 1:
                                    q['answer'] = q['answer'].upper()
                            
                            # 确保options包含选项字母前缀
                            if 'options' in q:
                                for i, option in enumerate(q['options']):
                                    letter = chr(65 + i)  # A, B, C, ...
                                    if not option.startswith(f"{letter}.") and not option.startswith(f"{letter}. "):
                                        q['options'][i] = f"{letter}. {option}"
                            
                            # 确保有类别标记
                            if 'category' not in q or not q['category']:
                                # 根据问题内容推断类别
                                question_text = q['question'].lower()
                                if any(word in question_text for word in ['流行病', '发病率', '死亡率', '患病率', '地区分布']):
                                    q['category'] = "流行病学"
                                elif any(word in question_text for word in ['病因', '危险因素', '诱因', '吸烟', '职业暴露']):
                                    q['category'] = "病因学"
                                elif any(word in question_text for word in ['病理', '分级', '分期', 'tnm', '组织学']):
                                    q['category'] = "病理学"
                                elif any(word in question_text for word in ['治疗', '手术', '放疗', '化疗', '免疫治疗']):
                                    q['category'] = "治疗"
                                elif any(word in question_text for word in ['预防', '筛查', '早期发现', '随访']):
                                    q['category'] = "预防"
                                elif any(word in question_text for word in ['诊断', '影像学', '实验室检查']):
                                    q['category'] = "诊断"
                                else:
                                    # 默认类别，根据指南类型推断
                                    if "流行病学" in category:
                                        q['category'] = "流行病学"
                                    else:
                                        q['category'] = "流行病学"  # 默认类别
                            
                            # 如果指定了特定类别，过滤掉不符合的问题
                            if specific_category and q.get('category') != specific_category:
                                q['category'] = specific_category  # 强制设置为指定类别
                        
                        return questions_json
                    else:
                        logger.warning("未能从API响应中提取JSON格式")
                        if attempt == max_retries - 1:  # 最后一次尝试
                            # 返回原始内容，后续处理将进行转换
                            return content
                except json.JSONDecodeError as e:
                    logger.warning(f"JSON解析失败: {str(e)}")
                    if attempt == max_retries - 1:  # 最后一次尝试
                        # 返回原始内容，后续处理将进行转换
                        return content
                
            elif response.status_code == 429:  # 速率限制
                wait_time = min(2 ** (attempt + 1), 60)  # 指数退避策略
                logger.warning(f"API速率限制，等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
            else:
                logger.error(f"API请求失败: {response.status_code} - {response.text}")
                time.sleep(5)
        except Exception as e:
            logger.error(f"API请求异常: {str(e)}")
            logger.error(traceback.format_exc())
            time.sleep(5)
    
    logger.error(f"在 {max_retries} 次尝试后仍未能生成问题")
    return None

# 将文本格式的问题转换为JSON格式
def convert_text_to_json(text_content, specific_category=None):
    try:
        questions = []
        # 使用正则表达式匹配问题格式
        question_pattern = r'(\d+)[、\.](.*?)\n(A\.(.*?)\n)(B\.(.*?)\n)(C\.(.*?)\n)(D\.(.*?)\n)(E\.(.*?)\n).*?正确答案：([A-E])'
        
        matches = re.finditer(question_pattern, text_content, re.DOTALL)
        
        match_count = 0
        for match in matches:
            match_count += 1
            question_text = match.group(2).strip()
            options = [
                f"A. {match.group(4).strip()}",
                f"B. {match.group(6).strip()}",
                f"C. {match.group(8).strip()}",
                f"D. {match.group(10).strip()}",
                f"E. {match.group(12).strip()}"
            ]
            answer_letter = match.group(13).strip()
            
            # 推断类别
            category = "流行病学"  # 默认类别
            question_lower = question_text.lower()
            if specific_category:
                category = specific_category
            elif any(word in question_lower for word in ['流行病', '发病率', '死亡率', '患病率', '地区分布']):
                category = "流行病学"
            elif any(word in question_lower for word in ['病因', '危险因素', '诱因', '吸烟', '职业暴露']):
                category = "病因学"
            elif any(word in question_lower for word in ['病理', '分级', '分期', 'tnm', '组织学']):
                category = "病理学"
            elif any(word in question_lower for word in ['治疗', '手术', '放疗', '化疗', '免疫治疗']):
                category = "治疗"
            elif any(word in question_lower for word in ['预防', '筛查', '早期发现', '随访']):
                category = "预防"
            elif any(word in question_lower for word in ['诊断', '影像学', '实验室检查']):
                category = "诊断"
            
            question_obj = {
                "question": question_text,
                "options": options,
                "answer": answer_letter,
                "explanation": f"根据指南内容，{answer_letter}. {match.group(2 + 2 * (ord(answer_letter) - ord('A') + 1)).strip()}是正确答案。",
                "category": category
            }
            
            questions.append(question_obj)
        
        logger.info(f"从文本中提取了 {match_count} 个匹配项，生成了 {len(questions)} 个问题对象")
        return questions
    except Exception as e:
        logger.error(f"转换问题格式出错: {str(e)}")
        logger.error(traceback.format_exc())
        return []

# 处理一个PDF文件并生成问题
def process_pdf_and_generate_questions(pdf_path, guide_category, total_questions=100, specific_category=None):
    pdf_filename = os.path.basename(pdf_path)
    guide_name = os.path.splitext(pdf_filename)[0]
    
    logger.info(f"开始处理 {guide_name}...")
    
    # 检查是否已存在输出文件
    category_suffix = f"_{specific_category}" if specific_category else ""
    output_file = output_path / f"{guide_category}_{guide_name}_膀胱癌问题{category_suffix}.json"
    if output_file.exists():
        logger.info(f"文件 {output_file} 已存在，跳过处理")
        return
    
    # 提取PDF文本
    text = extract_pdf_text(pdf_path)
    
    if not text or len(text.strip()) < 500:  # 如果文本太少，可能是提取失败
        logger.error(f"未能从 {pdf_path} 提取足够的文本，跳过处理")
        return
    
    # 将文本分成小块
    chunks = chunk_text(text)
    
    if not chunks:
        logger.error(f"未能将文本分成块，跳过处理")
        return
    
    # 计算每个块需要生成的问题数量
    questions_per_chunk = max(1, total_questions // len(chunks))
    remaining_questions = total_questions % len(chunks)
    
    all_questions = []
    questions_generated = 0
    
    for i, chunk in enumerate(chunks):
        # 检查是否已达到总问题数
        if questions_generated >= total_questions:
            logger.info(f"已生成 {questions_generated} 个问题，达到目标数量 {total_questions}")
            break
            
        # 计算这个块需要生成的问题数
        remaining_to_generate = total_questions - questions_generated
        q_count = min(questions_per_chunk, remaining_to_generate)
        if i == len(chunks) - 1:  # 最后一个块
            q_count = remaining_to_generate
            
        if q_count <= 0:
            continue
            
        logger.info(f"正在为块 {i+1}/{len(chunks)} 生成 {q_count} 个问题...")
        questions_result = generate_questions(chunk, guide_category, guide_name, q_count, specific_category=specific_category)
        
        if questions_result:
            # 检查返回的是JSON还是文本
            if isinstance(questions_result, list):
                # 已经是JSON格式
                chunk_questions = questions_result
            else:
                # 转换文本为JSON
                chunk_questions = convert_text_to_json(questions_result, specific_category)
            
            if chunk_questions:
                all_questions.extend(chunk_questions)
                questions_generated += len(chunk_questions)
                
                # 定期保存中间结果，以防程序中断
                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(all_questions, f, ensure_ascii=False, indent=2)
                logger.info(f"已保存中间结果到 {output_file}，当前共 {questions_generated}/{total_questions} 个问题")
            else:
                logger.warning(f"块 {i+1} 生成的问题无法解析为JSON格式")
        else:
            logger.warning(f"块 {i+1} 未能生成问题")
        
        # 避免API限流
        wait_time = 2
        logger.info(f"等待 {wait_time} 秒后继续...")
        time.sleep(wait_time)
    
    # 最终保存
    if all_questions:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(all_questions, f, ensure_ascii=False, indent=2)
        logger.info(f"已完成 {guide_name} 的处理，共生成 {questions_generated} 个问题，已保存到 {output_file}")
    else:
        logger.error(f"未能为 {guide_name} 生成任何问题")

# 主函数处理所有指南
def main():
    try:
        logger.info("=== 开始处理所有指南 ===")
        
        # 处理CA2025流行病学指南，只生成30个流行病学问题
        ca2025_path = guides_path / "流行病学CA2025"
        if ca2025_path.exists():
            for pdf_file in ca2025_path.glob("*.pdf"):
                process_pdf_and_generate_questions(pdf_file, "流行病学CA2025", total_questions=60)
        else:
            logger.warning(f"目录不存在: {ca2025_path}")
        
        # # 处理AUA指南
        # aua_path = guides_path / "AUA"
        # if aua_path.exists():
        #     for pdf_file in aua_path.glob("*.pdf"):
        #         process_pdf_and_generate_questions(pdf_file, "AUA")
        # else:
        #     logger.warning(f"目录不存在: {aua_path}")
        
        # # 处理EAU指南
        # eau_path = guides_path / "EAU"
        # if eau_path.exists():
        #     for pdf_file in eau_path.glob("*.pdf"):
        #         process_pdf_and_generate_questions(pdf_file, "EAU")
        # else:
        #     logger.warning(f"目录不存在: {eau_path}")
        
        # # 处理NCCN指南
        # nccn_path = guides_path / "NCCN"
        # if nccn_path.exists():
        #     for pdf_file in nccn_path.glob("*.pdf"):
        #         process_pdf_and_generate_questions(pdf_file, "NCCN")
        # else:
        #     logger.warning(f"目录不存在: {nccn_path}")
        
        # logger.info("=== 所有指南处理完成 ===")
    except Exception as e:
        logger.error(f"主函数运行时出错: {str(e)}")
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    main()
