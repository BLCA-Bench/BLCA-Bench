import os
import sys
import pdfplumber
import logging
from pathlib import Path

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("pdf_fallback.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def extract_with_pdfplumber(pdf_path, output_path=None):
    """
    使用pdfplumber提取PDF文本，作为PyPDF2的备用方案
    
    Args:
        pdf_path: PDF文件路径
        output_path: 可选的输出文件路径，如果不提供则返回文本
        
    Returns:
        提取的文本，如果output_path为None
    """
    try:
        logger.info(f"使用pdfplumber提取 {pdf_path}")
        text = ""
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
                    logger.warning(f"无法提取第 {i+1} 页: {str(e)}")
        
        logger.info(f"文本提取完成，总长度: {len(text)} 字符")
        
        # 如果提供了输出路径，则保存到文件
        if output_path:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(text)
            logger.info(f"文本已保存到 {output_path}")
            return None
        else:
            return text
    except Exception as e:
        logger.error(f"提取PDF文本时出错: {str(e)}")
        return ""

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python pdf_extract_fallback.py <pdf文件路径> [输出文件路径]")
        sys.exit(1)
        
    pdf_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    if not os.path.exists(pdf_path):
        logger.error(f"文件不存在: {pdf_path}")
        sys.exit(1)
        
    extract_with_pdfplumber(pdf_path, output_path)
