"""Boss直聘自动化系统 - Streamlit Web界面"""

import sys
import time
from pathlib import Path

import streamlit as st
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.runner import manager

# ==================== 页面配置 ====================
st.set_page_config(
    page_title="Boss直聘自动化",
    page_icon="🎯",
    layout="wide",
)

CONFIG_PATH = Path(__file__).parent / "config" / "config.yaml"
PROFILE_PATH = Path(__file__).parent / "config" / "profile.yaml"


def load_profile():
    if PROFILE_PATH.exists():
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def save_profile(profile: dict):
    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        yaml.dump(profile, f, allow_unicode=True, default_flow_style=False)


def load_app_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def save_app_config(config: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)


if "profile" not in st.session_state:
    st.session_state.profile = load_profile()


# ==================== 侧边栏：控制面板 ====================
with st.sidebar:
    st.title("🎯 Boss直聘自动化")
    st.divider()

    # 启停控制
    st.subheader("爬虫控制")
    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("▶ 启动", type="primary", use_container_width=True,
                      disabled=manager.status == "running"):
            manager.load_config()
            manager.start()
            st.toast("爬虫启动中...")

    with col2:
        if st.button("⏸ 暂停", use_container_width=True,
                      disabled=manager.status != "running"):
            manager.pause()

    with col3:
        if st.button("⏹ 停止", use_container_width=True,
                      disabled=manager.status == "stopped"):
            manager.stop()

    status_colors = {"running": "🟢", "paused": "🟡", "stopped": "🔴", "error": "❌"}
    st.metric("状态", f"{status_colors.get(manager.status, '⚪')} {manager.status}")

    st.divider()

    # 总体统计
    st.subheader("总体统计")
    stats = manager.stats
    st.metric("已扫描", stats["total_scanned"])
    col_m, col_r = st.columns(2)
    with col_m:
        st.metric("匹配", stats["matched"])
    with col_r:
        st.metric("不合适", stats["rejected"])
    st.metric("已发消息", stats["messages_sent"])
    if stats["last_scan_time"]:
        st.caption(f"上次扫描: {stats['last_scan_time']}")

    st.divider()

    # 各岗位独立统计
    if manager.job_slots:
        st.subheader("各岗位统计")
        for i, slot in enumerate(manager.job_slots):
            title = slot.job.get("title", f"岗位{i+1}")
            icon = "✅" if slot.enabled else "⬜"
            with st.expander(f"{icon} {title}"):
                s = slot.stats
                c1, c2, c3 = st.columns(3)
                c1.metric("扫描", s["scanned"])
                c2.metric("匹配", s["matched"])
                c3.metric("发送", s["sent"])

    st.divider()
    st.caption("Boss直聘自动化 v2.0 - 多岗位并行")


# ==================== 主区域：标签页 ====================
tab_jobs, tab_rules, tab_logs = st.tabs(["📋 岗位管理", "⚙️ 筛选规则", "📜 运行日志"])


# ---------- Tab 1: 岗位管理 ----------
with tab_jobs:
    st.header("岗位管理")
    st.caption("启动爬虫后自动从Boss直聘抓取所有在招岗位的JD，也可手动编辑")

    # 自动抓取的岗位
    if manager.job_slots:
        st.subheader(f"在招岗位（共 {len(manager.job_slots)} 个）")

        for i, slot in enumerate(manager.job_slots):
            title = slot.job.get("title", f"岗位{i+1}")
            enabled = st.checkbox(
                f"启用筛选", value=slot.enabled,
                key=f"job_enabled_{i}",
            )
            if enabled != slot.enabled:
                manager.toggle_job(i, enabled)

            col_info, col_jd = st.columns([1, 2])

            with col_info:
                st.markdown(f"**{title}**")
                if slot.job.get("summary"):
                    st.text(slot.job["summary"][:200])
                s = slot.stats
                st.markdown(f"扫描 {s['scanned']} | 匹配 {s['matched']} | 拒绝 {s['rejected']}")

            with col_jd:
                jd_text = slot.profile.get("job_description", "")
                new_jd = st.text_area(
                    "岗位JD",
                    value=jd_text,
                    height=180,
                    key=f"job_jd_{i}",
                )

            # 标准问题
            questions_text = "\n".join(slot.questions)
            new_questions = st.text_area(
                "标准问题（每行一个）",
                value=questions_text,
                height=80,
                key=f"job_questions_{i}",
            )

            if st.button(f"💾 保存 [{title}] 的JD和问题", key=f"save_job_{i}"):
                manager.update_job_profile(i, {
                    "job_description": new_jd,
                    "questions": [q.strip() for q in new_questions.split("\n") if q.strip()],
                })
                st.toast(f"已保存 [{title}] 的JD和问题")

            st.divider()
    else:
        st.info("👆 点击侧边栏「▶ 启动」后，会自动从Boss直聘抓取你发布的所有岗位JD")

    # 手动添加岗位（备用）
    with st.expander("➕ 手动添加岗位（如果自动抓取失败）"):
        new_title = st.text_input("岗位名称", key="add_job_title")
        new_jd = st.text_area("岗位JD", height=150, key="add_job_jd")
        new_qs = st.text_area("标准问题（每行一个）", height=80, key="add_job_qs")
        if st.button("添加岗位"):
            if new_title:
                slot = type('JobSlot', (), {})()
                from src.runner import JobSlot
                slot = JobSlot(
                    {"title": new_title, "description": new_jd},
                    manager.profile or st.session_state.profile,
                    manager.llm_client,
                )
                slot.questions = [q.strip() for q in new_qs.split("\n") if q.strip()]
                manager.job_slots.append(slot)
                st.toast(f"已添加岗位: {new_title}")
                st.rerun()


# ---------- Tab 2: 筛选规则（全局共享） ----------
with tab_rules:
    st.header("筛选规则（所有岗位共用）")
    st.caption("每个岗位用自己的JD筛选，但硬筛选规则（学历/经验/薪资等）是共用的")

    profile = st.session_state.profile
    rules = profile.get("rules", {})

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("硬筛选条件")

        edu_options = ["大专", "本科", "硕士", "博士"]
        current_edu = rules.get("min_education", "本科")
        edu_index = edu_options.index(current_edu) if current_edu in edu_options else 1
        rules["min_education"] = st.selectbox("最低学历", edu_options, index=edu_index)

        rules["min_experience"] = st.number_input(
            "最低工作年限", min_value=0, max_value=30,
            value=rules.get("min_experience", 3),
        )

        rules["max_age"] = st.number_input(
            "最大年龄", min_value=20, max_value=65,
            value=rules.get("max_age", 45),
        )

        salary_range = rules.get("salary_range", [15000, 50000])
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            salary_min = st.number_input("最低月薪", value=salary_range[0], step=1000)
        with col_s2:
            salary_max = st.number_input("最高月薪", value=salary_range[1], step=1000)
        rules["salary_range"] = [salary_min, salary_max]

    with col_right:
        st.subheader("技能与关键词")

        rules["required_skills"] = [
            s.strip() for s in st.text_area(
                "必备技能（每行一个）",
                value="\n".join(rules.get("required_skills", [])),
                height=100,
            ).split("\n") if s.strip()
        ]

        rules["preferred_skills"] = [
            s.strip() for s in st.text_area(
                "优先技能（每行一个）",
                value="\n".join(rules.get("preferred_skills", [])),
                height=100,
            ).split("\n") if s.strip()
        ]

        rules["blacklist_keywords"] = [
            s.strip() for s in st.text_area(
                "黑名单关键词（每行一个）",
                value="\n".join(rules.get("blacklist_keywords", [])),
                height=80,
            ).split("\n") if s.strip()
        ]

    st.divider()

    col_llm, col_msg = st.columns(2)

    with col_llm:
        st.subheader("LLM筛选")
        rules["llm_score_threshold"] = st.slider(
            "LLM匹配分数阈值",
            min_value=0, max_value=100,
            value=rules.get("llm_score_threshold", 60),
            help="LLM评分低于此值的简历将被拒绝",
        )

    with col_msg:
        st.subheader("消息模板")
        app_config = load_app_config()
        messaging = app_config.get("messaging", {})
        new_matched = st.text_area(
            "匹配消息模板",
            value=messaging.get("matched_template", ""),
            height=80,
        )
        new_rejected = st.text_area(
            "婉拒消息模板",
            value=messaging.get("rejected_template", ""),
            height=80,
        )

    if st.button("💾 保存筛选规则", type="primary"):
        profile["rules"] = rules
        st.session_state.profile = profile
        save_profile(profile)
        # 同步更新所有岗位的规则
        for i in range(len(manager.job_slots)):
            manager.update_job_profile(i, {
                "rules": rules,
                "llm_score_threshold": rules.get("llm_score_threshold", 60),
            })
        # 保存消息模板
        app_config.setdefault("messaging", {})
        app_config["messaging"]["matched_template"] = new_matched
        app_config["messaging"]["rejected_template"] = new_rejected
        save_app_config(app_config)
        st.toast("筛选规则和消息模板已保存！")


# ---------- Tab 3: 运行日志 ----------
@st.fragment(run_every="3s")
def render_logs():
    st.header("运行日志")

    if manager.logs:
        display_logs = manager.logs[-100:]
        for log in display_logs:
            if "错误" in log or "异常" in log or "失败" in log:
                st.error(log, icon="❌")
            elif "✅" in log or ("匹配" in log and "不匹配" not in log):
                st.success(log, icon="✅")
            elif "❌" in log or "不匹配" in log:
                st.warning(log, icon="⚠️")
            else:
                st.text(log)
    else:
        st.info("暂无日志，启动爬虫后日志将显示在这里")

with tab_logs:
    render_logs()
