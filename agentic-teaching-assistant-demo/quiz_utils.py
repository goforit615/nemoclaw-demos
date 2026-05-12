"""
Quiz parsing utilities.

These functions parse quiz data from LLM responses into standardized format.
"""
import re
from colorama import Fore


def extract_choices_robust(choice_str):
    """Extract choices from a string with robust pattern matching."""
    # First, try to separate concatenated choices
    # Look for patterns like ')''(' and insert space
    separated = re.sub(r"\)'\s*'\s*\(", ")' '(", choice_str)
    
    # Clean the string
    cleaned = re.sub(r"[\[\]'\"\\]", " ", separated)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    
    # Extract all choices - more flexible pattern
    pattern = r"\([A-E]\)[^(\]]*?(?=\s*\([A-E]\)|\s*$)"
    matches = re.findall(pattern, cleaned)
    
    result = [match.strip() for match in matches if match.strip()]
    return result


def _double_check(choice):
    """Helper to validate and extract choices from a string."""
    choices = choice  # Default fallback
    
    if '(A)' in choice and '(B)' in choice and '(C)' in choice and '(D)' in choice and '(E)' in choice:
        choices = get_choices({"choices": choice})
    elif '(A)' in choice and '(B)' in choice and '(C)' in choice and '(D)' in choice:
        choices = get_choices({"choices": choice})
    elif '(A)' in choice and '(B)' in choice and '(C)' in choice:
        choices = get_choices({"choices": choice})
    elif '(A)' in choice and '(B)' in choice:
        choices = get_choices({"choices": choice})
    elif '(A)' in choice:
        choices = choice
    elif '(B)' in choice:
        choices = choice
    elif '(C)' in choice:
        choices = choice
    elif '(D)' in choice:
        choices = choice
    elif '(E)' in choice:
        choices = choice
    
    return choices


def get_choices(quiz_d):
    """
    Extract choices from a quiz dict.
    
    Args:
        quiz_d: Quiz item dict with 'choices' key
        
    Returns:
        List of choice strings
    """
    choice_str = quiz_d["choices"]
    
    # If already a list, return as-is
    if isinstance(choice_str, list):
        return choice_str
    
    choice_text = str(choice_str).replace('[', '').replace(']', '').replace("'", "").replace('"', '').strip()
    lines = [line.strip() for line in choice_text.split('\n') if line.strip()]
    
    all_choices = []
    for line in lines:
        # Handle the specific format where choices are concatenated
        # Replace ')''(' with ') ' (' to separate them properly
        fixed = re.sub(r"\)'\s*'\s*\(", ")' '(", line)
        
        # Clean up
        cleaned = ""
        for char in fixed:
            if char not in "[]":
                cleaned += char
        
        # Remove extra quotes and normalize
        cleaned = cleaned.replace("'", " ").replace("\\", " ")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        
        # Extract choices
        pattern = r"\([A-E]\)[^(\]]*"
        matches = re.findall(pattern, cleaned)
        
        result = [match.strip() for match in matches if match.strip()]
        final_result = [_double_check(item) for item in result]
        
        if isinstance(final_result, list):
            all_choices.extend([ch.strip() for ch in final_result if ch.strip()])
        else:
            all_choices.append(str(final_result).strip())
    
    if len(all_choices) < 4:
        print(Fore.RED + f"extracted= {len(all_choices)} {all_choices}" + Fore.RESET)
    
    return all_choices


def get_question(quiz_d):
    """
    Extract question text from a quiz dict.
    
    Args:
        quiz_d: Quiz item dict with 'question' key
        
    Returns:
        Question string
    """
    question_str = quiz_d["question"]
    question = question_str.replace('[', '').replace(']', '').replace("'", "")
    return question


def get_answer(quiz_d):
    """
    Extract answer from a quiz dict.
    
    Args:
        quiz_d: Quiz item dict with 'answer' key
        
    Returns:
        Answer in format like '(A)', '(B)', etc.
    """
    answer_str = quiz_d["answer"]
    answer = answer_str.replace('[', '').replace(']', '').replace("'", "")
    
    answer_to_index = {"A": '(A)', "B": '(B)', "C": '(C)', "D": '(D)', "E": '(E)'}
    
    if answer in answer_to_index:
        return answer_to_index[answer]
    
    # If already in (X) format, return as-is
    if answer.startswith('(') and answer.endswith(')'):
        return answer
    
    print(Fore.YELLOW + f"Warning: answer '{answer}' not in standard format" + Fore.RESET)
    return answer


def get_citation_as_explain(quiz_d):
    """
    Extract citation/explanation from a quiz dict.
    
    Args:
        quiz_d: Quiz item dict with 'citations' and 'thought_process' keys
        
    Returns:
        Explanation string
    """
    citation_ls = quiz_d.get("citations", [])
    
    if isinstance(citation_ls, list):
        citation_str = '\n'.join(citation_ls)
    else:
        citation_str = citation_ls
    
    thought_process = quiz_d.get("thought_process", "")
    explanation = f"reference to source: {citation_str} and thought_process: {thought_process}"
    return explanation

