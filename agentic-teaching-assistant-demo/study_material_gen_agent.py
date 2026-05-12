import os
import asyncio
import aiohttp
import json
import requests
from langchain_nvidia_ai_endpoints import NVIDIAEmbeddings, NVIDIARerank
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain_core.runnables import RunnablePassthrough
import concurrent.futures
from colorama import Fore
import os,json
import argparse
from openai import OpenAI
from llm import create_llm

import base64
from PIL import Image
import io
from IPython.display import Markdown, display
import markdown
#from search_and_filter_documents import filter_documents_by_file_name
from search_and_filter_docs_streaming import filter_documents_by_file_name

def printmd(markdown_str):
    display(Markdown(markdown_str))

def strip_thinking_tag(response):
    # Handle None or empty responses
    if response is None or not response:
        print(Fore.RED + "ERROR: Received None or empty response in strip_thinking_tag" + Fore.RESET)
        return ""
    
    if "</think>" in response:
        end_index = response.index("</think>")+8
        output = response[end_index:]
        return output
    else:
        return response


def strip_quiz_content(content: str) -> str:
    """
    Remove quiz-like sections from study material content.
    
    The app generates its own quizzes, so we strip out any quiz content
    that may be present in the source PDF to avoid duplication.
    
    Removes sections that contain:
    - "Practice and reflection"
    - "Mini-quiz"
    - "Quiz"
    - "Test yourself"
    - Multiple choice patterns (a), b), c), d))
    """
    import re
    
    if not content:
        return content
    
    # Patterns that indicate the start of a quiz section
    quiz_section_patterns = [
        r'\n\s*#+\s*Practice and reflection.*',
        r'\n\s*#+\s*Mini-quiz.*',
        r'\n\s*#+\s*Quiz.*',
        r'\n\s*#+\s*Test yourself.*',
        r'\n\s*#+\s*Self-assessment.*',
        r'\n\s*#+\s*Review questions.*',
        r'\n\s*\*\*Practice and reflection\*\*.*',
        r'\n\s*\*\*Mini-quiz\*\*.*',
        r'\n\s*\*\*Quiz\*\*.*',
        r'\n\s*Practice and reflection\s*\n.*',
        r'\n\s*Mini-quiz\s*\n.*',
    ]
    
    # Try to find and remove quiz sections (everything after the quiz header)
    for pattern in quiz_section_patterns:
        match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
        if match:
            # Remove everything from this point to the end
            content = content[:match.start()].rstrip()
            print(Fore.YELLOW + f"[FILTER] Removed quiz section starting with pattern: {pattern[:50]}..." + Fore.RESET)
            break
    
    # Also remove standalone multiple choice questions that might appear inline
    # Pattern: question followed by a), b), c), d) options
    mc_pattern = r'\n[^a-z\n]*\?\s*\n\s*a\)\s*[^\n]+\n\s*b\)\s*[^\n]+\n\s*c\)\s*[^\n]+\n\s*d\)\s*[^\n]+'
    content = re.sub(mc_pattern, '', content, flags=re.IGNORECASE)
    
    return content.strip()


study_material_gen_prompts= PromptTemplate(
    template=("""
    You are an expert pedagogical educator who specializes in designing high-quality study materials.  
    Your goal is to help learners achieve mastery in the main subject: {subject}.  
    
    Focus particularly on the sub-topic: {sub_topic}.  
    You will be given contextual details to guide the content creation: {detail_context}.  
    
    Your task is to create clear, engaging, and well-structured study material for the specified sub-topic.  
    Ensure the material:
    - Supports the learner’s progression toward mastering the main subject.  
    - Explains complex ideas in a simple and accessible manner.  
    - Includes examples, key definitions, and summaries where appropriate.  
    - Encourages critical thinking and retention.  
    
    IMPORTANT CONSTRAINTS:
    - Write ONLY educational study content - do NOT include JSON code, API endpoints, collection names, or technical documentation
    - Do NOT include phrases like "POST /collection", "Collection Name:", or any API-related instructions
    - Focus exclusively on explaining the subject matter to students
    - Use natural language and markdown formatting only for headings, lists, and emphasis
    
    Always maintain educational clarity, logical flow, and learner engagement.
    Begin""")
)

async def study_material_gen(username, subject, sub_topic, pdf_file_name, num_docs, pdf_path=None):
    valid_flag = False
    cnt = 0
    num_docs = 3
    output = ""
    img_str = ""
    while valid_flag == False or cnt <= 3:
        valid_flag, output, img_str = await filter_documents_by_file_name(username, sub_topic, pdf_file_name, num_docs)
        print("got valid output =", valid_flag, valid_flag == False)
        if valid_flag:
            break
        elif cnt >= 1:
            break
        cnt += 1
    if not valid_flag:
        valid_flag, output, img_str = await filter_documents_by_file_name(username, sub_topic, None, num_docs)

    if not valid_flag or not output:
        raise RuntimeError(
            f"[study_material_gen] RAG server returned no chunks for sub_topic={sub_topic!r}. "
            "The RAG stack is mandatory — check ingestor (port 8082) and rag-server (port 8081) health."
        )
    if isinstance(output,str):
        detail_context=output
        study_material_generation_prompt_formatted=study_material_gen_prompts.format(subject=subject, sub_topic=sub_topic, detail_context=detail_context)

        # Use LLM service for study material generation
        llm = create_llm("study_material_generation")

        # Log LLM details
        import time
        print(Fore.CYAN + "=" * 80)
        print(Fore.CYAN + "🤖 LLM CALL: study_material_generation")
        print(Fore.CYAN + f"   Provider: {getattr(llm, 'model', 'unknown')}")
        print(Fore.CYAN + f"   Start time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(Fore.CYAN + f"   Prompt length: {len(study_material_generation_prompt_formatted)} chars")
        print(Fore.CYAN + "=" * 80)

        start_time = time.time()
        try:
            llm_response = await llm.ainvoke(study_material_generation_prompt_formatted)
            elapsed = time.time() - start_time
            print(Fore.GREEN + "=" * 80)
            print(Fore.GREEN + f"✅ LLM RESPONSE SUCCESS")
            print(Fore.GREEN + f"   Duration: {elapsed:.2f} seconds")
            print(Fore.GREEN + f"   Response length: {len(llm_response.content)} chars")
            print(Fore.GREEN + "=" * 80)
        except Exception as e:
            elapsed = time.time() - start_time
            print(Fore.RED + "=" * 80)
            print(Fore.RED + f"❌ LLM CALL FAILED")
            print(Fore.RED + f"   Duration: {elapsed:.2f} seconds")
            print(Fore.RED + f"   Error: {type(e).__name__}: {str(e)}")
            print(Fore.RED + "=" * 80)
            raise

        llm_parsed_output = llm_response.content
        #print(Fore.BLUE + "using new LLM client > llm parsed relevent_chunks as context output=\n", llm_parsed_output) 
        #print("---"*10)
        study_material_str=strip_thinking_tag(llm_parsed_output)
        # Remove any quiz content from the study material (app generates its own quizzes)
        study_material_str=strip_quiz_content(study_material_str)
        if img_str:
            
            markdown_str = markdown.markdown(f'''                
                {study_material_str}

                <br/><br/>
                Reference_document:{pdf_file_name}
                <br/><br/>
                Reference_images :
                {img_str}               
                ''')
        else:
            markdown_str = markdown.markdown(f'''                
                {study_material_str}
                
                <br/><br/>
                Reference_document:{pdf_file_name}
                ''')

        print(Fore.BLUE + "stripped thinking tag output=\n", study_material_str, Fore.RESET) 
        print("---"*10)
        return study_material_str, markdown_str
    elif isinstance(output,ls) :   
        if len(output)>0:
            detail_context='\n'.join([f"detail_context:{o['metadata']['description']}" for o in output if o['document_type']=="text"])
        study_material_generation_prompt_formatted=study_material_gen_prompts.format(subject=subject, sub_topic=sub_topic, detail_context=detail_context)
        
        # Use LLM service for study material generation
        llm = create_llm("study_material_generation")
        llm_response = await llm.ainvoke(study_material_generation_prompt_formatted)
        llm_parsed_output = llm_response.content
        #print(Fore.BLUE + "using new LLM client > llm parsed relevent_chunks as context output=\n", llm_parsed_output) 
        #print("---"*10)
        study_material_str=strip_thinking_tag(llm_parsed_output)
        # Remove any quiz content from the study material (app generates its own quizzes)
        study_material_str=strip_quiz_content(study_material_str)
        
        reference_images_base64_str='\n'.join([f"""<br><p align='center'><img src='data:image/png;base64,{o['content']}'/></p></br>""" for o in output if o['document_type'] in ["image", "table", "chart"] ])
        markdown_str = markdown.markdown(f'''                
            {study_material_str}
            
            
            Reference_document:{pdf_file_name}
            
            Reference_images :
            {reference_images_base64_str}               
            ''')

        print(Fore.BLUE + "stripped thinking tag output=\n", study_material_str, Fore.RESET) 
        print("---"*10)
        
        return study_material_str , markdown_str
    else:        
        print(Fore.BLUE + "using build.nvidia.com's llm call > llm parsed relevent_chunks as context output=\n", output) 
        print("---"*10)
        #output=strip_thinking_tag(output)
        output=""
        print(Fore.BLUE + "stripped thinking tag output=\n", output, Fore.RESET) 
        print("---"*10)
        
        return output, ""
if __name__ == "__main__":
    # Move top-level async calls into an async main to avoid 'await outside function'
    #query = "fetch information on driving in highway/mortorway"
    query = "\n1: Learning Techniques for Driving - Awareness, Overlearning, and Deep Insight."
    pdf_file = "SwedenDriving_intro.pdf"
    subject=pdf_file.split('.pdf')[0]
    sub_topic="**chapter_title:**18: Driving License Regulations, Requirements & Exceptions"
    num_docs=5
    output, markdown_str =asyncio.run( study_material_gen(subject,sub_topic, pdf_file, num_docs))
    print(type(output), Fore.GREEN + "output=\n\n",output)
    