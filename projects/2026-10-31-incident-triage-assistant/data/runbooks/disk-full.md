# Disk usage critical
## Symptoms
Writes fail with "no space left on device" or ENOSPC. Services crash while writing logs, temp files or WAL segments.
## Mitigation
1. Run `df -h` and `du -xh --max-depth=1` on the full mount to find the biggest directory.
2. Rotate and compress logs; delete files in the tmp directory older than 7 days.
3. If the volume is a cloud disk, expand it online and grow the filesystem.
4. Add an alert at 80 percent usage to prevent recurrence.
