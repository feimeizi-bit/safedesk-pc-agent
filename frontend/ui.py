import gradio as gr
import requests

API_URL = "http://127.0.0.1:8000/chat"

def respond(message, chat_history):
    """处理用户消息并更新聊天记录（字典格式）"""
    pending_token = None
    pending_text = "当前没有待确认计划。"
    try:
        resp = requests.post(
            API_URL,
            json={"text": message},
            timeout=90,
        )
        resp.raise_for_status()
        payload = resp.json()
        bot_msg = payload.get("response", "抱歉，没有回复。")
        pending_token = payload.get("confirmation_token")
        if pending_token:
            pending_text = (
                f"待确认计划：`{payload.get('plan_hash', '')[:12]}`。"
                "请核对上方预览，再点击“确认执行预览计划”。"
            )
    except Exception as e:
        bot_msg = f"❌ 请求失败: {str(e)}"

    # 追加字典格式的消息
    chat_history.append({"role": "user", "content": message})
    chat_history.append({"role": "assistant", "content": bot_msg})
    return "", chat_history, pending_token, pending_text


def confirm_pending_plan(chat_history, pending_token):
    """Execute the exact persisted plan without asking the model to plan again."""
    if not pending_token:
        chat_history.append({"role": "assistant", "content": "当前没有可确认的操作计划。"})
        return chat_history, None, "当前没有待确认计划。"
    try:
        resp = requests.post(
            API_URL,
            json={"confirmation_token": pending_token},
            timeout=90,
        )
        resp.raise_for_status()
        payload = resp.json()
        bot_msg = payload.get("response", "抱歉，没有执行结果。")
    except Exception as e:
        bot_msg = f"❌ 确认执行失败: {str(e)}"
    chat_history.append({"role": "assistant", "content": bot_msg})
    return chat_history, None, "当前没有待确认计划。"


def clear_session():
    return [], None, "当前没有待确认计划。"

def show_audit_log():
    try:
        resp = requests.get("http://127.0.0.1:8000/audit_log", timeout=10)
        logs = resp.json()["logs"]
        if not logs:
            return "暂无审计日志。"
        table = "| 时间 | 工具 | 参数 | 结果 | 已确认 |\n|------|------|------|------|--------|\n"
        for log in logs:
            table += f"| {log['timestamp']} | {log['tool']} | {log['params']} | {log['result']} | {'是' if log['confirmed'] else '否'} |\n"
        return table
    except Exception as e:
        return f"获取日志失败: {e}"

with gr.Blocks(title="PC Agent - 本地智能助手") as demo:
    gr.Markdown("# 🤖 PC Agent 本地智能助手")
    gr.Markdown("这是一个完全在本地运行的 PC 助手，可以帮你整理文件、查询系统信息、打开应用等。")
    
    # 注意：不要添加 type="messages" 参数，但 Chatbot 会自动适配字典格式
    chatbot = gr.Chatbot(label="对话记录", height=500)
    msg = gr.Textbox(label="输入你的指令", placeholder="例如：帮我整理桌面文件", lines=2)
    pending_token = gr.State(None)
    pending_output = gr.Markdown("当前没有待确认计划。")
    
    with gr.Row():
        send_btn = gr.Button("发送", variant="primary")
        confirm_btn = gr.Button("确认执行预览计划", variant="stop")
        clear_btn = gr.Button("清空对话")
        audit_btn = gr.Button("查看审计日志")
    
    audit_output = gr.Markdown()
    
    send_btn.click(
        respond,
        [msg, chatbot],
        [msg, chatbot, pending_token, pending_output],
    )
    msg.submit(
        respond,
        [msg, chatbot],
        [msg, chatbot, pending_token, pending_output],
    )
    confirm_btn.click(
        confirm_pending_plan,
        [chatbot, pending_token],
        [chatbot, pending_token, pending_output],
    )
    clear_btn.click(
        clear_session,
        None,
        [chatbot, pending_token, pending_output],
        queue=False,
    )
    audit_btn.click(show_audit_log, outputs=audit_output)

if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7861, share=False)
