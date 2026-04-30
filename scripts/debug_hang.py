"""
调试脚本：直接运行 DeviationFlow，观察在 deviation_analyzer 之后卡在哪
"""
import sys
import time
import traceback
import threading

PROJECT_ROOT = "/mnt/c/Users/顾/Desktop/Project/bid_eval"
sys.path.insert(0, PROJECT_ROOT)

fake_tender = "招标要求：服务器主频不低于2.6GHz，内存容量不低于768GB。"
fake_bid = "投标响应：服务器主频实际2.4GHz，内存实际512GB。"

# Hook: 定期打印当前线程的 stack trace
def print_stack_every(interval=15):
    for i in range(20):
        time.sleep(interval)
        print(f"\n[STACK TRACE @ {i*interval}s]")
        for tid, frame in sys._current_frames().items():
            print(f"\nThread {tid}:")
            traceback.print_stack(frame)
        print("[/STACK TRACE]\n")

stack_thread = threading.Thread(target=print_stack_every, daemon=True)
stack_thread.start()

# 用 DeviationFlow 来运行，和 render_analyzing 保持一致
from src.bid_eval.main import DeviationFlow, DeviationFlowState, Project

flow = DeviationFlow()
flow.state.project = Project(
    project_id="debug_hang",
    project_name="调试项目",
    tender_files=[],
    bid_files=[],
)
# tender_content/bid_content 已在 state 中，kickoff 使用这些
flow.state.tender_content = fake_tender
flow.state.bid_content = fake_bid

print("Agent created, starting kickoff...")

# 使用与 main.py 的 run_analysis 中 kickoff 一致的 inputs
result = flow.kickoff(inputs={
    "project_id":       "debug_hang",
    "tender_content":   fake_tender,
    "bid_content":      fake_bid,
    "requirements_json": "",
    "responses_json":    "",
})

print("\n===== kickoff 返回 =====")
print(type(result))
print(result)