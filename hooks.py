import subprocess

def on_page_markdown(markdown, **kwargs):
    """
    Hook to dynamically replace {{ version }} with the latest git tag.
    """
    try:
        # Fetch the latest tag from git
        tag = subprocess.check_output(
            ['git', 'describe', '--tags', '--abbrev=0'], 
            stderr=subprocess.DEVNULL
        ).decode('utf-8').strip()
    except Exception:
        tag = "vUnknown"
    
    return markdown.replace("{{ version }}", tag)
