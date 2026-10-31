# Container out of memory
## Symptoms
Pods restart with exit code 137 and reason OOMKilled. Kernel logs show "Out of memory: Killed process". Heap usage climbs steadily.
## Mitigation
1. Confirm with `kubectl describe pod` that the last state is OOMKilled.
2. Capture a heap dump or memory profile from a sibling pod before restart.
3. Raise the memory limit temporarily and roll back the most recent release if the leak began after it.
4. Check for unbounded caches and large request payloads.
