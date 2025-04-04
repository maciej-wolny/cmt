#!/usr/bin/env python3
import subprocess
import os
from typing import List, Tuple
import sys
import json
import multiprocessing

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
    try:
        # Get modified files that are not ignored
        modified = subprocess.check_output([
            'git', 'diff', '--name-only', '--diff-filter=d',
            '--ignore-space-at-eol', '--no-ext-diff'
        ]).decode('utf-8').split('\n')
        
        # Get untracked files that are not ignored
        untracked = subprocess.check_output([
            'git', 'ls-files', '--others',
            '--exclude-from=.gitignore',  # Use .gitignore rules
            '--exclude=.idea',  # Explicitly exclude .idea directory
            '--exclude=.idea/*'  # Explicitly exclude all files in .idea
        ]).decode('utf-8').split('\n')
        
        # Combine files and filter
        all_files = [f for f in modified + untracked if f]
        
        # Additional explicit filtering for .idea files
        filtered_files = [
            f for f in all_files 
            if not f.startswith('.idea/') and f != '.idea'
        ]
        
        return filtered_files
        
    except subprocess.CalledProcessError as e:
        print(f"Error getting changed files: {e}")
        return []

def get_file_diff(file_path: str) -> str:
    """Get the diff for a specific file."""
    try:
        if os.path.exists(file_path):
            # Check if file is tracked
            result = subprocess.run(['git', 'ls-files', '--error-unmatch', file_path],
                                 capture_output=True)
            is_tracked = result.returncode == 0
            
            if is_tracked:
                diff = subprocess.check_output(['git', 'diff', file_path]).decode('utf-8')
                return diff if diff else f"No changes in tracked file: {file_path}"
            else:
                # For untracked files, mark as new and get content
                with open(file_path, 'r') as f:
                    return f"NEW_FILE:{file_path}\n" + f.read()
        return ""
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""

def generate_commit_message(file_path: str, diff: str, debug_mode: bool = False) -> Tuple[str, str, str]:
    """Generate commit message for a single file."""
    try:
        diff_text = f"File: {file_path}\n{diff}"
        
        # Handle new files differently
        if "NEW_FILE:" in diff:
            return file_path, "feat: add new file", None
            
        prompt = f"""
Write a concise and informative commit message for the following changes shown in the diff. This prompt is consumed programmaticaly so provide raw commit, without explaination.
1. Begin with a short summary of the changes in imperative mood. It should aways start with a sinlge sentence.
2. Clearly explain why these changes were made, but only if it's directly apparent from the diff.
3. If multiple changes, Use bullet points to list multiple distinct changes if applicable.
4. Ensure the message includes **only** the changes present in the diff, with no additional context, explanations, or assumptions.
5. Avoid any speculative details or information that is not directly reflected in the changes of the diff. The message should be focused solely on the modifications made.
Diff:\n\n{diff_text}
"""
        
        # Add timeout to curl request
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
        try:
            result = json.loads(response.stdout)
        except json.JSONDecodeError:
            return file_path, "chore: automated commit", f"Invalid JSON response from LLM"
            
        full_response = result['response'].strip()
        
        if debug_mode:
            print(f"\nDEBUG: Full LLM Response for {file_path}:")
            print("-" * 40)
            print(full_response)
            print("-" * 40)
        
        # Handle Deepseek's thinking tags
        if "<think>" in full_response and "</think>" in full_response:
            message = full_response.split("</think>")[-1].strip()
        else:
            message = full_response
            
        # Clean up the message
        lines = message.split('\n')
        first_line = lines[0].strip()
        
        # If the first line is just backticks, take the next non-empty line
        if first_line.startswith('```'):
            for line in lines[1:]:
                line = line.strip()
                if line and not line.startswith('```'):
                    first_line = line
                    break
        
        # If message is empty after processing, use default
        if not first_line:
            return file_path, "chore: update file", None
            
        return file_path, first_line[:50], None
        
    except subprocess.TimeoutExpired:
        return file_path, "chore: automated commit", "LLM request timed out"
    except Exception as e:
        return file_path, "chore: automated commit", f"Error: {str(e)}"

def commit_and_push(file_path: str, message: str):
    """Commit a single file and push to the current branch."""
    try:
        # Check if file is ignored by any .gitignore
        check_ignored = subprocess.run(
            ['git', 'check-ignore', '-q', file_path],
            capture_output=True
        )
        
        if check_ignored.returncode == 0:
            print(f"Skipping {file_path}: File is ignored by .gitignore")
            raise subprocess.CalledProcessError(
                1, 
                f"File {file_path} is ignored by .gitignore rules. Skipping commit.",
                stderr=b"File is ignored by .gitignore"
            )
        
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
        if "ignored by one of your .gitignore files" in str(e.stderr):
            print(f"Skipping {file_path}: File is ignored by .gitignore")
        else:
            print(f"Error in git operations for {file_path}: {e}")
        raise

def generate_readme(files: List[str], debug_mode: bool = False) -> str:
    """Generate README.md content based on project files."""
    try:
        # Get repository information
        repo_name = os.path.basename(get_git_root())
        
        # Collect file structure and content samples
        file_structure = {}
        for file in files:
            dir_name = os.path.dirname(file) or "root"
            if dir_name not in file_structure:
                file_structure[dir_name] = []
            file_structure[dir_name].append(os.path.basename(file))
        
        # Create prompt for README generation
        files_info = "\n".join([f"Directory {dir_name}:\n" + "\n".join(f"- {f}" for f in files) 
                               for dir_name, files in file_structure.items()])
        
        prompt = f"""Generate a comprehensive README.md file for this project. Include the following sections:
1. Project name and brief description
2. Project structure
3. Installation instructions
4. Usage instructions
5. Requirements (if any)

Project name: {repo_name}
Files structure:
{files_info}

Respond with only the README content in markdown format."""

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
        full_response = result['response'].strip()
        
        if debug_mode:
            print("\nDEBUG: Full README Response:")
            print("-" * 40)
            print(full_response)
            print("-" * 40)
        
        # Handle Deepseek's thinking tags
        if "<think>" in full_response and "</think>" in full_response:
            content = full_response.split("</think>")[-1].strip()
        else:
            content = full_response
            
        return content
        
    except Exception as e:
        print(f"Error generating README: {e}")
        return None

def update_readme(debug_mode: bool = False):
    """Update or create README.md file."""
    try:
        # Get all tracked files
        files = subprocess.check_output(['git', 'ls-files']).decode('utf-8').split('\n')
        files = [f for f in files if f]  # Remove empty strings
        
        # Generate README content
        content = generate_readme(files, debug_mode)
        if not content:
            print("Failed to generate README content")
            return False
            
        # Write to README.md
        with open('README.md', 'w') as f:
            f.write(content)
            
        print("README.md has been updated successfully")
        
        # Commit and push the README
        commit_and_push('README.md', "docs: update README.md")
        return True
        
    except Exception as e:
        print(f"Error updating README: {e}")
        return False

def main():
    # Parse command line arguments
    debug_mode = "-debug" in sys.argv
    readme_mode = "--readme" in sys.argv
    
    # Change to git root directory
    os.chdir(get_git_root())
    
    if readme_mode:
        update_readme(debug_mode)
        return
    
    # Get changed files
    changed_files = get_changed_files()
    if not changed_files:
        print("No changes to commit")
        return
    
    # Keep track of all commits and errors
    commit_summary = []
    
    # Process each file sequentially
    for file_path in changed_files:
        try:
            # Get diff for single file
            diff = get_file_diff(file_path)
            
            if debug_mode:
                print(f"\nDEBUG: Processing file: {file_path}")
                print(f"Diff:\n{diff}")
                print("-" * 80)
            
            # Generate commit message
            file_path, message, error = generate_commit_message(file_path, diff, debug_mode)
            
            if error:
                print(f"Error generating commit message for {file_path}: {error}")
                commit_summary.append((file_path, "FAILED", error))
                continue
                
            print(f"\nProcessing file: {file_path}")
            print(f"Committing with message: {message}")
            
            try:
                # Commit and push this file
                commit_and_push(file_path, message)
                commit_summary.append((file_path, message, None))
            except subprocess.CalledProcessError as e:
                error_msg = str(e)
                if "ignored by .gitignore" in error_msg:
                    commit_summary.append((file_path, "SKIPPED", "File ignored by .gitignore"))
                else:
                    commit_summary.append((file_path, "FAILED", f"Git error: {error_msg}"))
                continue

            # Handle terraform formatting
            if file_path.endswith('.tf'):
                try:
                    with open(file_path, 'r') as f:
                        content_before = f.read()
                    
                    subprocess.run(['terraform', 'fmt', file_path], check=True)
                    
                    with open(file_path, 'r') as f:
                        content_after = f.read()
                    
                    if content_before != content_after:
                        print(f"Formatted terraform file: {file_path}")
                        try:
                            commit_and_push(file_path, "tf fmt")
                            commit_summary.append((file_path, "tf fmt", None))
                        except subprocess.CalledProcessError as e:
                            error_msg = str(e)
                            if "ignored by .gitignore" in error_msg:
                                commit_summary.append((file_path, "SKIPPED", "File ignored by .gitignore"))
                            else:
                                commit_summary.append((file_path, "FAILED", f"Git error: {error_msg}"))
                    else:
                        print(f"No formatting changes needed for {file_path}")
                        
                except Exception as e:
                    error_msg = f"Terraform formatting error: {str(e)}"
                    print(f"Error: {error_msg}")
                    commit_summary.append((file_path, "tf fmt", error_msg))
                    
        except Exception as e:
            error_msg = f"Failed: {str(e)}"
            print(f"Error processing file {file_path}: {e}")
            commit_summary.append((file_path, "FAILED", error_msg))
            continue
    
    # Print summary at the end
    print("\n" + "="*80)
    print("Commit Summary:")
    print("="*80)
    for file_path, message, error in commit_summary:
        if error:
            print(f"{file_path:.<40} {message} ({error})")
        else:
            print(f"{file_path:.<40} {message}")
    print("="*80)

if __name__ == "__main__":
    main() 