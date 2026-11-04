import os
import docx
import requests
import json
import re
from pathlib import Path
import time
import traceback
import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
import tqdm

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("ehr_process.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# API配置（凭证在运行时从环境变量读取）
model_name = os.getenv("BLCA_MODEL", "gpt-4o")
api_key = os.getenv("BLCA_API_KEY", "")
api_url = os.getenv("BLCA_API_URL", "")

# 病历路径和输出路径
ehrs_path = Path("data/ehrs")
output_path = Path("output")
intermediate_path = Path("intermediate")  # 存储中间结果的路径

# 确保输出目录存在
os.makedirs(output_path, exist_ok=True)
os.makedirs(intermediate_path, exist_ok=True)

# 总问题计数器（多进程共享）
total_questions_count = multiprocessing.Value('i', 0)
MAX_QUESTIONS = 500  # 最大问题数量

# 从docx文件提取文本
def extract_docx_text(docx_path):
    try:
        logger.info(f"从 {docx_path} 提取文本...")
        doc = docx.Document(docx_path)
        text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
        logger.info(f"提取完成，文本长度: {len(text)} 字符")
        return text
    except Exception as e:
        logger.error(f"提取文本出错: {str(e)}")
        logger.error(traceback.format_exc())
        return ""

# 使用GPT API识别多个病人的第一份病历
def identify_patients_first_records(text, max_retries=3):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    prompt = f"""
    请分析以下泌尿外科病历文档，识别出其中所有病人的第一份完整病历记录。
    如果一个病人有多份病历记录，只提取该病人的第一份病历。
    每份病历应该包含从主诉、现病史开始到医生签名结束的完整内容。
    提取时去除患者姓名等隐私信息，但保留年龄、性别和诊断信息。
    
    请使用以下JSON格式输出提取的内容:
    {{
        "patients": [
            {{
                "patient_record": "完整的第一份病历内容，去除患者姓名等隐私信息",
                "patient_info": {{
                    "age": "患者年龄",
                    "gender": "患者性别",
                    "diagnosis": "初步诊断"
                }}
            }},
            ... 更多病人
        ]
    }}
    
    以下是病历内容的前3000字符（如内容较长，仅显示部分）：
    {text[:3000]}...
    
    请尽可能识别出所有病人的第一份病历，确保病历记录的完整性，并去除隐私信息。
    """
    
    data = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "你是一位医疗文档分析专家，擅长从复杂的病历文档中识别和提取多个病人的关键信息，同时保护患者隐私。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3
    }
    
    for attempt in range(max_retries):
        try:
            logger.info(f"发送API请求识别多个病人的第一份病历 (尝试 {attempt+1}/{max_retries})...")
            response = requests.post(api_url, headers=headers, data=json.dumps(data))
            
            if response.status_code == 200:
                result = response.json()
                
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
                
                logger.info(f"成功识别病历，返回内容长度: {len(content)} 字符")
                
                # 尝试提取JSON内容
                try:
                    # 使用正则表达式提取JSON部分
                    json_match = re.search(r'(\{[\s\S]*\})', content)
                    if json_match:
                        json_str = json_match.group(1)
                        result_json = json.loads(json_str)
                        
                        # 检查结果格式
                        if 'patients' in result_json and isinstance(result_json['patients'], list):
                            logger.info(f"识别到 {len(result_json['patients'])} 个病人的病历")
                            return result_json
                        else:
                            logger.warning("JSON格式不符合预期，缺少patients字段或格式错误")
                    else:
                        logger.warning("未能从API响应中提取JSON格式")
                except json.JSONDecodeError as e:
                    logger.warning(f"JSON解析失败: {str(e)}")
            
            elif response.status_code == 429:  # 速率限制
                wait_time = min(2 ** (attempt + 1), 60)
                logger.warning(f"API速率限制，等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
            else:
                logger.error(f"API请求失败: {response.status_code} - {response.text}")
                time.sleep(5)
        except Exception as e:
            logger.error(f"API请求异常: {str(e)}")
            logger.error(traceback.format_exc())
            time.sleep(5)
    
    # 如果无法识别，创建单病人的结果
    logger.warning("无法识别多个病历，将整个文本作为一个病历处理")
    return {
        "patients": [
            {
                "patient_record": text,
                "patient_info": {"age": "未知", "gender": "未知", "diagnosis": "未知"}
            }
        ]
    }

# 生成医学问题，按照示例格式，保留病历作为背景
def generate_medical_questions(ehr_content, patient_info, question_count=6, max_retries=3):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    # 准备病人基本信息（不包含姓名）
    patient_brief = f"{patient_info.get('gender', '未知')}，{patient_info.get('age', '未知')}岁"
    
    prompt = f"""
    我需要您基于以下泌尿外科病历内容，生成{question_count}道高质量的膀胱癌相关单选题。
    请不要在问题中包含患者姓名等隐私信息。
    
    患者基本信息：{patient_brief}
    
    请严格按以下要求生成问题：
    1. 只生成单选题，不要生成多选题
    2. 所有题目必须与膀胱癌相关，不要生成其他疾病的题目
    3. 每个问题必须描述清楚、充分且准确，需要包含足够的临床信息使其具有唯一答案
    4. 请按照以下四个类别分别生成题目：
       - 症状体征：关于膀胱癌的临床表现、症状、体征等
       - 辅助检查：关于膀胱癌的诊断检查方法、检查结果解读等
       - 治疗：关于膀胱癌的治疗方案、手术方式、药物治疗等
       - 预防：关于膀胱癌的预防、随访、复发风险控制等
    5. 每个问题有5个选项(A-E)
    6. 答案解释必须详细，包括为什么正确选项是正确的，以及为什么其他选项不正确
    
    请使用以下JSON格式输出：
    [
      {{
        "question": "男，52岁，于1周前无明显诱因出现无痛性肉眼血尿，呈间歇性，伴有尿频，无发热，首先考虑的诊断是？",
        "options": [
          "A. 肾结石",
          "B. 膀胱肿瘤", 
          "C. 泌尿系结核",
          "D. 急性膀胱炎",
          "E. 急性肾盂肾炎"
        ],
        "answer": "B",
        "explanation": "根据病史，无痛性肉眼血尿是膀胱肿瘤的典型表现，特别是间歇性无痛血尿。肾结石引起的血尿常伴随剧烈疼痛；泌尿系结核可有血尿但常有全身症状；急性膀胱炎通常有尿痛、尿急症状明显；急性肾盂肾炎常有高热、腰痛等表现。",
        "type": "single",
        "category": "症状体征",  // 新增类别标记：症状体征、辅助检查、治疗、预防
        "background": "完整的病历内容作为背景"  // 使用原始病历作为背景，不含患者姓名
      }},
      ...更多问题
    ]

    以下是病历内容（请作为问题的背景使用）：
    {ehr_content}
    
    生成的问题应该遵循这样的格式示例：
    1. 症状体征类："{patient_brief}，膀胱癌最常见的首发症状是？"
    2. 辅助检查类："膀胱癌的金标准诊断方法是？"
    3. 治疗类："非肌层浸润性膀胱癌的标准治疗方案是？"
    4. 预防类："膀胱癌术后为预防复发，建议随访的频率是？"
    
    确保每个问题的描述与病历内容紧密相关，准确提取关键临床信息，使问题描述清晰充分，有明确的临床推理过程，避免模糊或有争议的问题。
    所有题目必须与膀胱癌相关，不要生成与膀胱癌无关的题目。
    """
    
    data = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "你是一位顶级泌尿外科教授，擅长根据病历创建高质量的膀胱癌医学考试题目。你对膀胱癌的临床表现、诊断方法、治疗方案和预后随访具有丰富经验。你的问题需要描述清楚、充分、准确，符合临床实际。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.5  # 降低温度以获得更确定性的结果
    }
    
    for attempt in range(max_retries):
        try:
            logger.info(f"发送API请求生成问题 (尝试 {attempt+1}/{max_retries})...")
            response = requests.post(api_url, headers=headers, data=json.dumps(data))
            
            if response.status_code == 200:
                result = response.json()
                
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
                        
                        # 确保格式正确
                        for q in questions_json:
                            # 确保options包含选项字母前缀
                            if 'options' in q:
                                for i, option in enumerate(q['options']):
                                    letter = chr(65 + i)  # A, B, C, ...
                                    if not option.startswith(f"{letter}.") and not option.startswith(f"{letter}. "):
                                        q['options'][i] = f"{letter}. {option}"
                            
                            # 确保背景是完整的病历内容
                            if 'background' not in q or not q['background'] or len(q['background']) < 50:
                                q['background'] = ehr_content
                            
                            # 确保问题描述清楚、充分
                            if 'question' in q and len(q['question']) < 20:
                                logger.warning(f"问题描述过短: {q['question']}")
                                # 尝试改进问题描述
                                q['question'] = f"{patient_brief}，{q['question']}"
                            
                            # 确保解释详细
                            if 'explanation' in q and len(q['explanation']) < 50:
                                logger.warning(f"解释过短: {q['explanation']}")
                                # 尝试扩展解释
                                q['explanation'] = f"{q['explanation']} 这基于病历中的临床表现和医学知识判断。"
                            
                            # 确保所有题目都是单选题
                            q['type'] = "single"
                            
                            # 确保answer是单个字母
                            if isinstance(q.get('answer'), list):
                                q['answer'] = q['answer'][0] if q['answer'] else "A"
                            
                            # 确保有类别标记
                            if 'category' not in q or not q['category']:
                                # 根据问题内容推断类别
                                question_text = q['question'].lower()
                                if any(word in question_text for word in ['症状', '表现', '体征', '血尿']):
                                    q['category'] = "症状体征"
                                elif any(word in question_text for word in ['检查', '诊断', 'ct', '膀胱镜', '影像', '活检']):
                                    q['category'] = "辅助检查"
                                elif any(word in question_text for word in ['治疗', '手术', '电切', '膀胱灌注', '药物']):
                                    q['category'] = "治疗"
                                elif any(word in question_text for word in ['预防', '随访', '复发', '监测']):
                                    q['category'] = "预防"
                                else:
                                    q['category'] = "症状体征"  # 默认类别
                        
                        # 验证问题质量
                        valid_questions = []
                        for q in questions_json:
                            # 问题必须足够长且有意义
                            if len(q['question']) >= 20 and len(q['explanation']) >= 50:
                                valid_questions.append(q)
                            else:
                                logger.warning(f"跳过低质量问题: {q['question']}")
                        
                        if len(valid_questions) >= 3:  # 至少需要3个有效问题
                            return valid_questions
                        else:
                            logger.warning(f"有效问题数量不足: {len(valid_questions)}/6，将重试")
                            continue
                    else:
                        logger.warning("未能从API响应中提取JSON格式")
                except json.JSONDecodeError as e:
                    logger.warning(f"JSON解析失败: {str(e)}")
                    
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
    
    logger.error(f"在 {max_retries} 次尝试后仍未能生成高质量问题")
    return []

# 处理单个病历文件
def process_single_ehr(file_path, lock):
    try:
        file_name = os.path.basename(file_path)
        file_stem = os.path.splitext(file_name)[0]
        logger.info(f"开始处理 {file_name}...")
        
        # 检查是否已处理过此文件
        intermediate_file = intermediate_path / f"{file_stem}_patients_first_record.json"
        
        if intermediate_file.exists():
            # 如果已有中间文件，直接读取
            logger.info(f"从中间文件 {intermediate_file} 读取数据...")
            with open(intermediate_file, 'r', encoding='utf-8') as f:
                patients_data = json.load(f)
            patients = patients_data.get("patients", [])
        else:
            # 提取文本
            text = extract_docx_text(file_path)
            
            if not text or len(text.strip()) < 100:
                logger.error(f"未能从 {file_path} 提取足够的文本，跳过处理")
                return []
            
            # 识别多个病人的第一份病历
            patients_data = identify_patients_first_records(text)
            patients = patients_data.get("patients", [])
            
            # 保存中间结果
            with open(intermediate_file, 'w', encoding='utf-8') as f:
                json.dump(patients_data, f, ensure_ascii=False, indent=2)
            logger.info(f"已保存 {len(patients)} 个病人的第一份病历到 {intermediate_file}")
        
        all_patient_questions = []
        
        # 为每个病人生成问题
        for i, patient in enumerate(patients):
            # 检查是否已达到总问题数限制
            with lock:
                if total_questions_count.value >= MAX_QUESTIONS:
                    logger.info(f"已达到总问题上限 {MAX_QUESTIONS}，跳过生成问题")
                    break
            
            patient_record = patient.get("patient_record", "")
            patient_info = patient.get("patient_info", {})
            
            # 为单个病人生成问题
            logger.info(f"为文件 {file_name} 中的第 {i+1}/{len(patients)} 个病人生成问题...")
            
            # 为每个病人生成问题数量(最多6个)
            questions_per_patient = min(6, MAX_QUESTIONS - total_questions_count.value)
            if questions_per_patient <= 0:
                break
                
            questions = generate_medical_questions(
                patient_record, 
                patient_info,
                question_count=questions_per_patient
            )
            
            with lock:
                # 检查是否超过最大问题数
                remaining = MAX_QUESTIONS - total_questions_count.value
                if remaining <= 0:
                    break
                
                if len(questions) > remaining:
                    questions = questions[:remaining]
                
                # 更新计数器
                total_questions_count.value += len(questions)
                logger.info(f"文件 {file_name} 中第 {i+1} 个病人生成了 {len(questions)} 个问题，总数: {total_questions_count.value}/{MAX_QUESTIONS}")
            
            all_patient_questions.extend(questions)
        
        return all_patient_questions
    except Exception as e:
        logger.error(f"处理文件 {file_path} 时出错: {str(e)}")
        logger.error(traceback.format_exc())
        return []

# 主函数
def main():
    try:
        logger.info("=== 开始多进程处理病历文件 ===")
        
        # 获取所有docx文件
        ehr_files = list(ehrs_path.glob("*.docx"))
        
        if not ehr_files:
            logger.warning(f"目录 {ehrs_path} 中没有找到任何病历文件（.docx）")
            return
        
        logger.info(f"找到 {len(ehr_files)} 个病历文件")
        
        # 初始化共享锁
        lock = multiprocessing.Manager().Lock()
        
        # 初始化结果数组
        all_questions = []
        output_file = output_path / "all_questions.json"
        
        # 检查是否有现有问题
        if output_file.exists():
            try:
                with open(output_file, 'r', encoding='utf-8') as f:
                    all_questions = json.load(f)
                logger.info(f"从 {output_file} 加载了 {len(all_questions)} 个既有问题")
                # 更新计数器
                with lock:
                    total_questions_count.value = len(all_questions)
            except:
                logger.warning(f"无法读取 {output_file}，将创建新文件")
        
        # 使用进程池并行处理文件
        num_processes = min(multiprocessing.cpu_count(), len(ehr_files))
        logger.info(f"使用 {num_processes} 个进程并行处理")
        
        # 处理结果收集
        processed_results = []
        
        with ProcessPoolExecutor(max_workers=num_processes) as executor:
            # 提交所有任务
            futures = {executor.submit(process_single_ehr, file_path, lock): file_path for file_path in ehr_files}
            
            # 使用tqdm显示进度
            for future in tqdm.tqdm(futures, desc="处理病历文件"):
                try:
                    result = future.result()
                    processed_results.append(result)
                    
                    # 合并问题
                    questions_to_add = result
                    if questions_to_add:
                        all_questions.extend(questions_to_add)
                        
                        # 保存中间结果
                        with lock:
                            with open(output_file, "w", encoding="utf-8") as f:
                                json.dump(all_questions, f, ensure_ascii=False, indent=2)
                            logger.info(f"已保存中间结果，当前共 {len(all_questions)}/{MAX_QUESTIONS} 个问题")
                except Exception as e:
                    logger.error(f"获取任务结果时出错: {str(e)}")
        
        # 最终保存
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(all_questions, f, ensure_ascii=False, indent=2)
        
        logger.info(f"=== 处理完成，共生成 {len(all_questions)} 个问题，已保存到 {output_file} ===")
    
    except Exception as e:
        logger.error(f"主函数运行时出错: {str(e)}")
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    main()
