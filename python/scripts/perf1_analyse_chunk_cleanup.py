#!/usr/bin/env python3
import json
import sys

def analyse_log(log_path: str) -> None:
    """Parse transfer_status.log and report chunk cleanup times.
    
    Shows:
    - Per-chunk undo_close_ms and resume_redraw_ms
    - Total cleanup per chunk
    - Sum across all chunks
    - Average per chunk
    """
    
    with open(log_path, 'r') as f:
        events = []
        for line in f:
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    
    cleanup_events = [e for e in events if e.get('event') == 'telemetry.chunk_cleanup']
    
    if not cleanup_events:
        print("ERROR: No telemetry.chunk_cleanup events found in log.")
        print("Confirm Babysitter.jsx was updated with instrumentation.")
        sys.exit(1)
    
    redraw_times = []
    undo_times = []
    total_times = []
    
    print("\n=== CHUNK CLEANUP TELEMETRY ===\n")
    print(f"{'Chunk':<6} {'Undo (ms)':<12} {'Redraw (ms)':<12} {'Total (ms)':<12}")
    print("-" * 42)
    
    for ev in cleanup_events:
        idx = ev.get('chunk_index', '?')
        undo_ms = ev.get('undo_close_ms', 0)
        redraw_ms = ev.get('resume_redraw_ms', 0)
        total_ms = ev.get('total_cleanup_ms', 0)
        
        redraw_times.append(redraw_ms)
        undo_times.append(undo_ms)
        total_times.append(total_ms)
        
        print(f"{idx:<6} {undo_ms:<12.1f} {redraw_ms:<12.1f} {total_ms:<12.1f}")
    
    print("-" * 42)
    print(f"{'SUM':<6} {sum(undo_times):<12.1f} {sum(redraw_times):<12.1f} {sum(total_times):<12.1f}")
    print(f"{'AVG':<6} {sum(undo_times)/len(undo_times):<12.1f} {sum(redraw_times)/len(redraw_times):<12.1f} {sum(total_times)/len(total_times):<12.1f}")
    
    print("\n=== SUMMARY ===")
    print(f"Total chunks: {len(cleanup_events)}")
    print(f"Total undo time: {sum(undo_times):.0f} ms")
    print(f"Total redraw time: {sum(redraw_times):.0f} ms  ← **CULPRIT**")
    print(f"Total cleanup: {sum(total_times):.0f} ms ({sum(total_times)/1000:.1f}s)")
    if sum(total_times) > 0:
        print(f"\nHypothesis confirmation: resumeRedraw() is responsible for {sum(redraw_times)/sum(total_times)*100:.1f}% of cleanup time")

if __name__ == '__main__':
    log_path = sys.argv[1] if len(sys.argv) > 1 else 'transfer_status.log'
    analyse_log(log_path)
