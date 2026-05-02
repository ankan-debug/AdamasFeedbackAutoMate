# Automation

Python automation tool for Adamas University student portal tasks.

## Features

- **Feedback Submission**: Automatically submit course feedback for pending classes
- **Biometric Sync**: Sync attendance records with biometric system to fix mismatches

## Requirements

- Python 3.8+
- Virtual environment (recommended)

Install dependencies:

```bash
pip install -r requirements.txt
```

## Configuration

Edit `config.json` to customize behavior:

| Section | Key | Description |
|---------|-----|-------------|
| `credentials` | `username` | Student registration number |
| `credentials` | `password` | Student portal password |
| `urls` | `base_url` | Portal base URL (default: https://adamasknowledgecity.ac.in) |
| `ids` | `student_id` | Student ID number |
| `ids` | `survey_id` | Feedback survey ID (default: 4) |
| `feedback` | `question_ids` | Question IDs to answer |
| `feedback` | `ratings` | Rating values for each question |
| `feedback` | `comment` | Optional comment to submit |
| `biometric` | `enabled` | Enable biometric sync feature |
| `biometric` | `sync_mode` | "all" or "mismatch_only" (default) |
| `biometric` | `auto_refresh_after_sync` | Refresh status after sync |
| `subjects` | `include_only` | Only process these subject IDs (empty = all) |
| `subjects` | `exclude` | Skip these subject IDs |
| `timing` | `min_delay_seconds` | Minimum delay between requests |
| `timing` | `max_delay_seconds` | Maximum delay between requests |
| `timing` | `retry_delay_seconds` | Delay before retry on failure |
| `retries` | `http_max_retries` | HTTP request retry attempts |
| `retries` | `submit_max_retries` | Feedback submission retry attempts |
| `debug` | `enabled` | Enable debug logging |
| `debug` | `debug_dir` | Directory for debug output |

## Usage

```bash
python src/main.py
```

The tool presents an interactive menu:

```
============================================================
   Adamas University - Automation Tools
============================================================

  [1] Submit Feedback
  [2] Sync Biometric Attendance
  [3] Exit

Select option (1-3):
```

### Option 1: Submit Feedback

1. Logs into the student portal
2. Scrapes pending feedback classes from dashboard
3. Submits feedback with configured ratings
4. Reports success/failure counts

### Option 2: Sync Biometric Attendance

1. Fetches attendance records via AJAX
2. Identifies records where class shows "present" but biometric shows "absent"
3. Prompts before syncing each record
4. Updates biometric status via API

## Debug Output

When `debug.enabled` is true, the following are saved to the `debug/` directory:

- `run_info.json` - Run configuration
- `*.json` - Failure details with request/response data
- `*.html` - HTML response for failed requests
- `latest_failure.txt` - Path to most recent failure

## Security Notes

- Credentials are stored in plain text in `config.json`
- Add `config.json` to `.gitignore` if versioning code


### Made with 💗 by Ankan
