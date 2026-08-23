# Read-Only MCP

StudyHub Local includes a read-only MCP endpoint for local integrations.

## Tools

- `list_courses`
- `list_weeks`
- `list_files`
- `search_files`
- `search_content`
- `get_file_metadata`
- `read_file`
- `get_question`
- `search_study_library`
- `fetch_study_file`

## Boundaries

- Read-only only
- Localhost by default
- File access restricted to `STUDY_LIBRARY_PATH`
- Path traversal denied
- No upload, delete, Canvas submit, quiz submit, or assignment submit tools

Remote tunnel usage is intentionally not configured by default.
