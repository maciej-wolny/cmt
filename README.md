```markdown
# cmt

A simple tool for automating git commits with meaningful commit messages based on changed files.

## Project Structure

```
root/
├── .gitignore          # Git ignore file
├── README.md           # This document
└── auto_commit.py      # Main script for automated commits
```

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/cmt.git
   ```

2. Navigate to the project directory:
   ```bash
   cd cmt
   ```

3. Make the script executable (optional but recommended):
   ```bash
   chmod +x auto_commit.py
   ```

4. Add the script to your PATH (optional, for easier access):
   - Copy the script to a directory in your PATH:
     ```bash
     cp auto_commit.py /usr/local/bin/cmt
     ```

## Usage

1. Ensure you have Git installed on your system.

2. Run the tool from any directory:
   ```bash
   ./auto_commit.py  # or just `cmt` if added to PATH
   ```

3. The script will:
   - Check for modified files in the current directory (and subdirectories)
   - Generate a commit message based on file changes
   - Stage all changed files
   - Create a new commit with the generated message

4. Optional: Add `-v` or `--verbose` flag for more detailed output:
   ```bash
   ./auto_commit.py -v
   ```

## Requirements

- Python 3.x
- Git installed on your system

## Contributing

Contributions are welcome! Please fork the repository and submit a pull request.

## License

This project is under the MIT License. See the [LICENSE](LICENSE) file for more details.

## Acknowledgments

Thanks to all contributors who helped make this project better.
```