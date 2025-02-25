#!/usr/bin/env python3
import subprocess
import os
from typing import List, Tuple
import sys
import json

def get_git_root() -> str:
    """Get the root directory of the git repository."""
    try:
        root = subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], 
                                     stderr=subprocess.DEVNULL)
        return root.decode('utf-8').strip()
    except subprocess.CalledProcessError:
        print("Error: Not a git repository")
        sys.exit(1)

def get_changed_files() -> List[str]:
    """Get list of modified and untracked files."""
    # Get modified files
    modified = subprocess.check_output(['git', 'diff', '--name-only']).decode('utf-8').split('\n')
    # Get untracked files
    untracked = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'])\
        .decode('utf-8').split('\n')
    return [f for f in modified + untracked if f]

def get_file_diff(file_path: str) -> str:
    """Get the diff for a specific file."""
    try:
        if os.path.exists(file_path):
            # For tracked files
            diff = subprocess.check_output(['git', 'diff', file_path]).decode('utf-8')
            if not diff:
                # For untracked files, get the entire content
                with open(file_path, 'r') as f:
                    return f"New file: {file_path}\n" + f.read()
            return diff
        return ""
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""

def generate_commit_message(diffs: List[Tuple[str, str]]) -> str:
    """Generate a commit message using Ollama with deepseek-r1:32b model."""
    diff_text = "\n".join([f"File: {file}\n{diff}" for file, diff in diffs])
    
    try:
        # Use curl to make a request to local Ollama API
        prompt = f"""You are a helpful assistant that generates clear and concise git commit messages based on code diffs.
        Generate a concise commit message for these changes:
        {diff_text}"""
        
        response = subprocess.run([
            'curl', 
            '-X', 'POST',
            'http://localhost:11434/api/generate',
            '-d', json.dumps({
                "model": "deepseek-r1:32b",
                "prompt": prompt,
                "stream": False
            })
        ], capture_output=True, text=True, check=True)
        
        # Parse the JSON response
        result = json.loads(response.stdout)
        return result['response'].strip()
    except Exception as e:
        print(f"Error generating commit message: {e}")
        return "Update: Automated commit"

def commit_and_push(file_path: str, message: str):
    """Commit a single file and push to the current branch."""
    try:
        # Add specific file
        subprocess.run(['git', 'add', file_path], check=True)
        
        # Commit with generated message
        subprocess.run(['git', 'commit', '-m', message], check=True)
        
        # Get current branch
        branch = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD'])\
            .decode('utf-8').strip()
        
        # Push to current branch
        subprocess.run(['git', 'push', 'origin', branch], check=True)
        print(f"Successfully committed and pushed {file_path} to {branch}")
        
    except subprocess.CalledProcessError as e:
        print(f"Error in git operations for {file_path}: {e}")
        sys.exit(1)

def main():
    # Change to git root directory
    os.chdir(get_git_root())
    
    # Get changed files
    changed_files = get_changed_files()
    if not changed_files:
        print("No changes to commit")
        return
    
    # Process each file separately
    for file_path in changed_files:
        # Format terraform files before committing
        
        # Get diff for single file
        diff = get_file_diff(file_path)
        
        # Generate commit message for this file
        commit_message = generate_commit_message([(file_path, diff)])
        
        print(f"\nProcessing file: {file_path}")
        print(f"Committing with message: {commit_message}")
        
        # Commit and push this file
        commit_and_push(file_path, commit_message)

        if file_path.endswith('.tf'):
            try:
                subprocess.run(['terraform', 'fmt', file_path], check=True)
                # If formatting changed the file, get new diff
                formatted_diff = get_file_diff(file_path)
                if formatted_diff:
                    print(f"Formatted terraform file: {file_path}")
                    commit_and_push(file_path, "style: format terraform files")
            except subprocess.CalledProcessError as e:
                print(f"Warning: Could not format terraform file {file_path}: {e}")
if __name__ == "__main__":
    main() 