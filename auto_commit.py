#!/usr/bin/env python3
import subprocess
import os
from typing import List, Tuple
import sys
import json
import multiprocessing
import re

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
    """Get list of modified, untracked, and newly added files."""
    try:
        # Get modified files that are not ignored
        modified = subprocess.check_output([
            'git', 'diff', '--name-only', '--diff-filter=d',
            '--ignore-space-at-eol', '--no-ext-diff'
        ]).decode('utf-8').split('\n')
        
        # Get untracked files that are not ignored
        untracked = subprocess.check_output([
            'git', 'ls-files', '--others',
            '--exclude-standard'  # Respect all gitignore rules
        ]).decode('utf-8').split('\n')
        
        # Get newly added files (staged but not committed)
        newly_added = subprocess.check_output([
            'git', 'diff', '--name-only', '--cached'
        ]).decode('utf-8').split('\n')
        
        # Combine files and filter (exclude directories)
        all_files = [f for f in modified + untracked + newly_added if f and not os.path.isdir(f)]
        
        # Remove duplicates while preserving order
        seen = set()
        filtered_files = []
        for f in all_files:
            if f not in seen and not f.startswith('.idea/') and f != '.idea':
                seen.add(f)
                filtered_files.append(f)
        
        return filtered_files
        
    except subprocess.CalledProcessError as e:
        print(f"Error getting changed files: {e}")
        return []

def is_file_addition_or_deletion(file_path: str) -> Tuple[bool, str]:
    """Check if file is being added or deleted. Returns (is_addition_or_deletion, type)."""
    try:
        # Check if file exists
        file_exists = os.path.exists(file_path)
        
        # Check if file is tracked in git
        result = subprocess.run(['git', 'ls-files', '--error-unmatch', file_path],
                               capture_output=True)
        is_tracked = result.returncode == 0
        
        if file_exists and not is_tracked:
            # File exists but not tracked = addition
            return True, "addition"
        elif not file_exists and is_tracked:
            # File tracked but doesn't exist = deletion
            return True, "deletion"
        else:
            # File modification or no change
            return False, "modification"
            
    except Exception:
        return False, "unknown"

def get_file_diff(file_path: str) -> str:
    """Get the diff for a specific file."""
    try:
        if os.path.isdir(file_path):
            return ""
        if os.path.exists(file_path):
            # Check if file is tracked
            result = subprocess.run(['git', 'ls-files', '--error-unmatch', file_path],
                                 capture_output=True)
            is_tracked = result.returncode == 0
            
            if is_tracked:
                # Check both unstaged and staged changes
                diff = subprocess.check_output(['git', 'diff', file_path]).decode('utf-8')
                if not diff:
                    diff = subprocess.check_output(['git', 'diff', '--cached', file_path]).decode('utf-8')
                return diff
            else:
                # For untracked files, mark as new and get content
                with open(file_path, 'r') as f:
                    return f"NEW_FILE:{file_path}\n" + f.read()
        return ""
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""

def generate_commit_messages_batch(file_diffs: dict, debug_mode: bool = False) -> dict:
    """Generate commit messages for all files in a single LLM call.

    Args:
        file_diffs: dict mapping file_path -> diff string
        debug_mode: enable debug output

    Returns:
        dict mapping file_path -> commit message string
    """
    # Separate new files (no LLM needed) from modified files
    results = {}
    diffs_for_llm = {}

    for file_path, diff in file_diffs.items():
        if "NEW_FILE:" in diff:
            results[file_path] = "feat: add new file"
        else:
            diffs_for_llm[file_path] = diff

    if not diffs_for_llm:
        return results

    # Build combined diff block
    combined_diffs = "\n\n".join(
        f"=== FILE: {fp} ===\n{diff}" for fp, diff in diffs_for_llm.items()
    )
    file_list_json = json.dumps(list(diffs_for_llm.keys()))

    prompt = f"""Generate concise commit messages for each file following Conventional Commits format.

Rules:
- HEADER: <type>: <short description> (max 50 chars, imperative mood)
- Types: feat, fix, docs, style, refactor, test, chore
- Most commits should only have a header, add a body only if truly needed

Return a JSON object mapping each file path to its commit message.
The keys MUST be exactly these file paths: {file_list_json}

Return JSON:
{{
  "file_path_1": "type: description",
  "file_path_2": "type: description"
}}

Diffs:

{combined_diffs}
"""

    try:
        response = subprocess.run([
            'curl',
            '-X', 'POST',
            'http://localhost:11434/api/generate',
            '-d', json.dumps({
                "model": "qwen2.5-coder:7b",
                "prompt": prompt,
                "stream": False,
                "response_format": {
                    "type": "json_object"
                }
            })
        ], capture_output=True, text=True, check=True)

        result = json.loads(response.stdout)
        full_response = result['response'].strip()

        if debug_mode:
            print(f"\nDEBUG: Full batch LLM Response:")
            print("-" * 40)
            print(full_response)
            print("-" * 40)

        # Handle thinking tags
        if "<think>" in full_response and "</think>" in full_response:
            json_content = full_response.split("</think>")[-1].strip()
        else:
            json_content = full_response

        # Remove markdown code blocks
        if json_content.startswith('```json'):
            json_content = json_content[7:].strip()
        elif json_content.startswith('```'):
            json_content = json_content[3:].strip()
        if json_content.endswith('```'):
            json_content = json_content[:-3].strip()

        if debug_mode:
            print(f"DEBUG: Final JSON content to parse: '{json_content}'")

        if not json_content:
            for fp in diffs_for_llm:
                results[fp] = "chore: update file"
            return results

        commit_data = json.loads(json_content)

        for fp in diffs_for_llm:
            msg = commit_data.get(fp, '').strip() if isinstance(commit_data.get(fp), str) else ''
            results[fp] = msg if msg else "chore: update file"

        return results

    except Exception as e:
        if debug_mode:
            print(f"Error in batch commit message generation: {e}")
        for fp in diffs_for_llm:
            results[fp] = "chore: update file"
        return results

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
        
        # Commit only this specific file (not everything staged)
        subprocess.run(['git', 'commit', '-m', message, '--', file_path], check=True)
        
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
        
        prompt = f"""Generate a comprehensive README.md file for this project. Return as JSON format.

Include the following sections:
1. Project name and brief description
2. Project structure
3. Installation instructions
4. Usage instructions
5. Requirements (if any)

Project name: {repo_name}
Files structure:
{files_info}

Return JSON format:
{{
  "readme_content": "markdown content here"
}}

Respond with only the README content in markdown format inside the JSON."""

        response = subprocess.run([
            'curl', 
            '-X', 'POST',
            'http://localhost:11434/api/generate',
            '-d', json.dumps({
                "model": "deepseek-r1:32b",
                "prompt": prompt,
                "stream": False,
                "response_format": {
                    "type": "json_object"
                }
            })
        ], capture_output=True, text=True, check=True)
        
        # Parse the JSON response
        try:
            result = json.loads(response.stdout)
            full_response = result['response'].strip()
            
            if debug_mode:
                print("\nDEBUG: Full README Response:")
                print("-" * 40)
                print(full_response)
                print("-" * 40)
            
            # Handle Deepseek's thinking tags - extract content after </think>
            if "<think>" in full_response and "</think>" in full_response:
                json_content = full_response.split("</think>")[-1].strip()
            else:
                json_content = full_response
            
            # Parse the README JSON
            readme_data = json.loads(json_content)
            content = readme_data.get('readme_content', '').strip()
            
            if not content:
                return "# README\n\nProject documentation will be added here."
                
            return content
            
        except (json.JSONDecodeError, KeyError) as e:
            return f"# README\n\nProject documentation will be added here.\n\n<!-- JSON parsing error: {str(e)} -->"
        
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

def get_diff_with_develop(debug_mode: bool = False) -> Tuple[str, int]:
    """Get diff between current branch and develop. Returns (diff, token_count)."""
    try:
        # Get current branch
        current_branch = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD']).decode('utf-8').strip()
        
        # Check if develop branch exists
        result = subprocess.run(['git', 'show-ref', '--verify', '--quiet', 'refs/heads/develop'], 
                               capture_output=True)
        base_branch = 'develop' if result.returncode == 0 else 'main'
        
        if debug_mode:
            print(f"Current branch: {current_branch}")
            print(f"Base branch: {base_branch}")
        
        # Get diff
        diff = subprocess.check_output(['git', 'diff', f'{base_branch}...HEAD']).decode('utf-8')
        
        # Rough token estimation (1 token ≈ 4 characters)
        token_count = len(diff) // 4
        
        return diff, token_count
        
    except subprocess.CalledProcessError as e:
        if debug_mode:
            print(f"Error getting diff: {e}")
        return "", 0

def get_commit_messages_since_branch(base_branch: str = None, debug_mode: bool = False) -> List[str]:
    """Get all commit messages since branching from base."""
    try:
        if not base_branch:
            # Check if develop branch exists
            result = subprocess.run(['git', 'show-ref', '--verify', '--quiet', 'refs/heads/develop'], 
                                   capture_output=True)
            base_branch = 'develop' if result.returncode == 0 else 'main'
        
        # Get commit messages since base branch
        commits = subprocess.check_output([
            'git', 'log', f'{base_branch}..HEAD', '--pretty=format:%s'
        ]).decode('utf-8').split('\n')
        
        # Filter out empty lines
        commits = [c.strip() for c in commits if c.strip()]
        
        if debug_mode:
            print(f"Found {len(commits)} commits since {base_branch}")
            for i, commit in enumerate(commits, 1):
                print(f"  {i}. {commit}")
        
        return commits
        
    except subprocess.CalledProcessError as e:
        if debug_mode:
            print(f"Error getting commits: {e}")
        return []

def generate_mr_summary(diff: str = None, commits: List[str] = None, debug_mode: bool = False) -> str:
    """Generate MR summary based on diff or commits."""
    try:
        if diff and len(diff) > 0:
            # Use diff-based approach for detailed analysis
            prompt = f"""You are a technical writer creating a merge request summary. Analyze the provided git diff carefully and create a comprehensive summary.

Your task:
1. Review all the code changes in the diff
2. Identify the main purpose and scope of the changes
3. Understand what functionality was added, modified, or removed
4. Create a concise title that captures the essence of the changes
5. Write a detailed summary explaining what changed and why it matters

Focus on:
- New features or functionality added
- Bug fixes or improvements made
- Code refactoring or optimization
- Configuration or setup changes
- Documentation updates

Git diff to analyze:
{diff[:50000]}

JSON structure expected:
{{
  "title": "string (max 50 characters describing the main change)",
  "summary": "string (detailed explanation of what changed, why it changed, and the impact)"
}}"""
        elif commits and len(commits) > 0:
            # Use commit-based approach when diff is too long
            commits_text = "\n".join([f"- {commit}" for commit in commits])
            prompt = f"""You are a technical writer creating a merge request summary. Analyze the provided commit messages carefully and create a comprehensive summary.

Your task:
1. Review all the commit messages
2. Identify common themes and the overall goal
3. Group related changes together
4. Create a concise title that captures the main achievement
5. Write a detailed summary explaining what was accomplished and why

Focus on:
- New features or functionality added
- Bug fixes or improvements made
- Code refactoring or optimization
- Configuration or setup changes
- Documentation updates

Commit messages to analyze:
{commits_text}

JSON structure expected:
{{
  "title": "string (max 50 characters describing the main accomplishment)",
  "summary": "string (detailed explanation of what was accomplished, why it was done, and the overall impact)"
}}"""
        else:
            return "No changes detected for merge request"
        
        if debug_mode:
            print("DEBUG: Generating MR summary...")
        
        response = subprocess.run([
            'curl',
            '-X', 'POST',
            'http://localhost:11434/api/generate',
            '-d', json.dumps({
                "model": "deepseek-r1:32b",
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": 0.3,
                    "top_p": 0.9
                }
            })
        ], capture_output=True, text=True, check=True)
        
        # Parse the JSON response
        try:
            result = json.loads(response.stdout)
            full_response = result['response'].strip()
            
            if debug_mode:
                print("DEBUG: Full MR Response:")
                print("-" * 40)
                print(full_response)
                print("-" * 40)
            
            # Extract JSON from response - try multiple strategies
            json_content = full_response
            
            # Remove thinking tags first
            if "<think>" in json_content and "</think>" in json_content:
                json_content = json_content.split("</think>")[-1].strip()
            
            # Remove markdown code blocks
            if json_content.startswith('```json'):
                json_content = json_content[7:].strip()
            elif json_content.startswith('```'):
                json_content = json_content[3:].strip()
            
            if json_content.endswith('```'):
                json_content = json_content[:-3].strip()
            
            # Find JSON object in the text
            json_match = re.search(r'\{[^{}]*"title"[^{}]*"summary"[^{}]*\}', json_content)
            if json_match:
                json_content = json_match.group()
            else:
                # Fallback: try to find any JSON object
                json_match = re.search(r'\{.*?\}', json_content, re.DOTALL)
                if json_match:
                    json_content = json_match.group()
            
            if debug_mode:
                print(f"DEBUG: Extracted JSON: '{json_content}'")
            
            # Try to parse the JSON
            if json_content and json_content.strip():
                try:
                    mr_data = json.loads(json_content)
                    title = mr_data.get('title', '').strip()
                    summary = mr_data.get('summary', '').strip()
                    
                    if title and summary:
                        return f"# {title}\n\n{summary}"
                except json.JSONDecodeError:
                    pass
            
            # If JSON parsing fails, try to extract title and summary manually
            title_match = re.search(r'"title":\s*"([^"]+)"', full_response)
            summary_match = re.search(r'"summary":\s*"([^"]+)"', full_response)
            
            if title_match and summary_match:
                title = title_match.group(1).strip()
                summary = summary_match.group(1).strip()
                return f"# {title}\n\n{summary}"
            
            # Final fallback: create summary from commits or diff analysis
            if commits and len(commits) > 0:
                # Generate simple summary from commits
                if len(commits) == 1:
                    return f"# {commits[0][:50]}\n\nSingle commit with changes ready for review."
                else:
                    return f"# Multiple updates ({len(commits)} commits)\n\nThis PR includes {len(commits)} commits with various improvements and changes."
            else:
                return f"# Code updates\n\nChanges have been made and are ready for review."
            
        except (json.JSONDecodeError, KeyError) as e:
            if debug_mode:
                print(f"JSON parsing error: {e}")
            
            # Fallback: try to create summary from available data
            if commits and len(commits) > 0:
                if len(commits) == 1:
                    return f"# {commits[0][:50]}\n\nSingle commit ready for review."
                else:
                    return f"# Multiple updates ({len(commits)} commits)\n\nThis PR includes {len(commits)} commits with improvements."
            else:
                return f"# Code updates\n\nChanges ready for review."
        
    except Exception as e:
        if debug_mode:
            print(f"Error generating MR summary: {e}")
        
        # Emergency fallback using git data
        if commits and len(commits) > 0:
            return f"# {commits[0][:50]}\n\nChanges based on recent commits."
        else:
            return f"# Branch updates\n\nCode changes ready for merge."

def refine_mr_summary(current_summary: str, guidance: str, debug_mode: bool = False) -> str:
    """Refine MR summary based on user guidance."""
    try:
        prompt = f"""You are refining a merge request summary based on user feedback.

Current MR summary:
{current_summary}

User's guidance for changes:
{guidance}

Apply the user's requested changes to improve the MR summary. Keep the same JSON format.

JSON structure expected:
{{
  "title": "string (max 50 characters describing the main change)",
  "summary": "string (detailed explanation of what changed, why it changed, and the impact)"
}}"""

        response = subprocess.run([
            'curl',
            '-X', 'POST',
            'http://localhost:11434/api/generate',
            '-d', json.dumps({
                "model": "deepseek-r1:32b",
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": 0.3,
                    "top_p": 0.9
                }
            })
        ], capture_output=True, text=True, check=True)

        result = json.loads(response.stdout)
        full_response = result['response'].strip()

        if debug_mode:
            print("DEBUG: Refined MR Response:")
            print("-" * 40)
            print(full_response)
            print("-" * 40)

        json_content = full_response
        if "<think>" in json_content and "</think>" in json_content:
            json_content = json_content.split("</think>")[-1].strip()

        if json_content.startswith('```json'):
            json_content = json_content[7:].strip()
        elif json_content.startswith('```'):
            json_content = json_content[3:].strip()
        if json_content.endswith('```'):
            json_content = json_content[:-3].strip()

        json_match = re.search(r'\{[^{}]*"title"[^{}]*"summary"[^{}]*\}', json_content)
        if json_match:
            json_content = json_match.group()
        else:
            json_match = re.search(r'\{.*?\}', json_content, re.DOTALL)
            if json_match:
                json_content = json_match.group()

        if json_content and json_content.strip():
            try:
                mr_data = json.loads(json_content)
                title = mr_data.get('title', '').strip()
                summary = mr_data.get('summary', '').strip()
                if title and summary:
                    return f"# {title}\n\n{summary}"
            except json.JSONDecodeError:
                pass

        return current_summary

    except Exception as e:
        if debug_mode:
            print(f"Error refining MR summary: {e}")
        return current_summary

def validate_mr_summary(mr_summary: str, debug_mode: bool = False) -> Tuple[bool, str]:
    """Interactive validation of MR summary. Returns (approved, final_summary)."""
    while True:
        print("\n" + "=" * 80)
        print("Generated MR Summary:")
        print("=" * 80)
        print(mr_summary)
        print("=" * 80)
        print("\nOptions:")
        print("  [y] Approve and create MR")
        print("  [n] Cancel MR creation")
        print("  [e] Edit with guidance (provide instructions to refine)")
        print()

        try:
            choice = input("Your choice [y/n/e]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return False, mr_summary

        if choice == 'y':
            return True, mr_summary
        elif choice == 'n':
            print("MR creation cancelled.")
            return False, mr_summary
        elif choice == 'e':
            try:
                print("\nEnter guidance for refining the MR (e.g., 'make title shorter', 'add more detail about the API changes'):")
                guidance = input("> ").strip()
                if guidance:
                    print("\nRefining MR summary...")
                    mr_summary = refine_mr_summary(mr_summary, guidance, debug_mode)
                else:
                    print("No guidance provided, keeping current summary.")
            except (EOFError, KeyboardInterrupt):
                print("\nCancelled.")
                return False, mr_summary
        else:
            print("Invalid choice. Please enter 'y', 'n', or 'e'.")

def create_merge_request(debug_mode: bool = False):
    """Create a merge request with current branch against develop/main."""
    try:
        # Get current branch
        current_branch = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD']).decode('utf-8').strip()
        
        # Check if develop branch exists, otherwise use main
        result = subprocess.run(['git', 'show-ref', '--verify', '--quiet', 'refs/heads/develop'], 
                               capture_output=True)
        base_branch = 'develop' if result.returncode == 0 else 'main'
        
        print(f"Creating merge request from {current_branch} to {base_branch}")
        
        # Get diff to check size
        diff, token_count = get_diff_with_develop(debug_mode)
        
        if not diff:
            print("No changes detected between branches")
            return False
        
        print(f"Diff size: ~{token_count} tokens")
        
        # Get commits for fallback
        commits = get_commit_messages_since_branch(base_branch, debug_mode)
        
        # Use appropriate strategy based on diff size
        if token_count > 128000:
            print("Diff too large, using commit-based summary...")
            mr_summary = generate_mr_summary(commits=commits, debug_mode=debug_mode)
        else:
            print("Using diff-based summary...")
            mr_summary = generate_mr_summary(diff=diff, commits=commits, debug_mode=debug_mode)
        
        if debug_mode:
            print("DEBUG: Generated MR Summary:")
            print("-" * 40)
            print(mr_summary)
            print("-" * 40)
        
        approved, mr_summary = validate_mr_summary(mr_summary, debug_mode)
        if not approved:
            return False
        
        # Check if glab, gh, or hub is available
        glab_available = subprocess.run(['which', 'glab'], capture_output=True).returncode == 0
        hub_available = subprocess.run(['which', 'hub'], capture_output=True).returncode == 0
        gh_available = subprocess.run(['which', 'gh'], capture_output=True).returncode == 0
        
        if glab_available:
            # Try glab directly — it will fail gracefully if the repo isn't on GitLab
            print("Using GitLab CLI (glab) to create merge request...")
            result = subprocess.run([
                'glab', 'mr', 'create',
                '--target-branch', base_branch,
                '--title', mr_summary.split('\n')[0].replace('# ', ''),
                '--description', mr_summary
            ], capture_output=True, text=True)

            if result.returncode == 0:
                print("Merge request created successfully!")
                print(result.stdout)
                return True
            else:
                print(f"Error creating merge request with glab: {result.stderr}")

        elif gh_available:
            # Use GitHub CLI
            print("Using GitHub CLI to create pull request...")
            result = subprocess.run([
                'gh', 'pr', 'create',
                '--base', base_branch,
                '--head', current_branch,
                '--title', mr_summary.split('\n')[0].replace('# ', ''),
                '--body', mr_summary
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                print("Pull request created successfully!")
                print(result.stdout)
            else:
                print(f"Error creating pull request: {result.stderr}")
                
        elif hub_available:
            # Use Hub CLI  
            print("Using Hub CLI to create pull request...")
            result = subprocess.run([
                'hub', 'pull-request',
                '--base', base_branch,
                '--head', current_branch,
                '--message', mr_summary
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                print("Pull request created successfully!")
                print(result.stdout)
            else:
                print(f"Error creating pull request: {result.stderr}")
        else:
            print("No supported CLI tool found (glab, gh, hub)")
            print("Install glab (GitLab) or gh (GitHub) for your platform.")
            print("\nHere's the generated merge request content:\n")
            print("="*80)
            print(mr_summary)
            print("="*80)
            print(f"\nManually create MR from '{current_branch}' to '{base_branch}' using the above content")
        
        return True
        
    except Exception as e:
        print(f"Error creating merge request: {e}")
        return False

def main():
    # Parse command line arguments
    debug_mode = "--debug" in sys.argv
    readme_mode = "--readme" in sys.argv
    mr_mode = "--mr" in sys.argv
    
    # Change to git root directory
    os.chdir(get_git_root())
    
    if readme_mode:
        update_readme(debug_mode)
        return
    
    if mr_mode:
        create_merge_request(debug_mode)
        return
    
    # Get changed files
    changed_files = get_changed_files()
    if not changed_files:
        print("No changes to commit")
        return
    
    # Collect diffs for all changed files
    file_diffs = {}
    file_operations = {}
    for file_path in changed_files:
        diff = get_file_diff(file_path)
        if not diff:
            if debug_mode:
                print(f"Skipping {file_path}: no changes detected")
            continue
        file_diffs[file_path] = diff
        _, operation_type = is_file_addition_or_deletion(file_path)
        file_operations[file_path] = operation_type
        if debug_mode:
            print(f"\nDEBUG: Collected diff for: {file_path} ({operation_type})")

    if not file_diffs:
        print("No changes to commit")
        return

    # Generate all commit messages in a single LLM call
    print(f"Generating commit messages for {len(file_diffs)} file(s)...")
    commit_messages = generate_commit_messages_batch(file_diffs, debug_mode)

    # Commit and push each file
    commit_summary = []
    for file_path, message in commit_messages.items():
        operation_type = file_operations.get(file_path, "modification")
        print(f"\nProcessing file: {file_path}")
        print(f"Operation: {operation_type}")
        print(f"Committing with message: {message}")

        try:
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