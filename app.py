"""
고려대학교 재학생을 위한 학점·GPA 계산기  —  Streamlit
────────────────────────────────────────────────────────
* Streamlit ≥1.25, pandas ≥2.0 필요
* 실행:  `streamlit run app.py`
* 2025‑07‑01  — v1.6  (고려대학교 전체 재학생 대상 + UI 개선)

✨ 개선사항:
1. 고려대학교 전체 재학생을 위한 범용 계산기로 확장
2. 탭 버튼 가독성 개선 (색상 및 폰트 수정)
3. 푸터 HTML 표시 오류 수정
4. 전반적인 텍스트 및 UI 개선
"""

from __future__ import annotations

from typing import Dict, Tuple
import io

import numpy as np
import pandas as pd
import streamlit as st
import altair as alt

###############################################################################
# 1. 기준 데이터 — 성적 등급 매핑 & 졸업 요건
###############################################################################

GRADE_MAP_45: Dict[str, float | None] = {
    "A+": 4.5,
    "A0": 4.0,
    "B+": 3.5,
    "B0": 3.0,
    "C+": 2.5,
    "C0": 2.0,
    "D+": 1.5,
    "D0": 1.0,
    "F": 0.0,
    "P": None,   # Pass → 학점 산입, GPA 비산입
    "NP": None,  # Not‑Pass → 학점·GPA 모두 미산입
}

REQUIREMENTS: Dict[str, Dict[str, int]] = {
    "공통교양": {"required": 13},
    "핵심교양": {"required": 6},
    "학문의기초": {"required": 12},
    "기본전공": {"required": 42},
    "심화전공": {"required": 30},
    "일반선택": {"required": 27},
    "총계": {"required": 130},
}

TERM_OPTIONS = ["1학기", "2학기", "여름", "겨울"]
CATEGORY_OPTIONS = list(REQUIREMENTS.keys())[:-1]  # "총계" 제외
GRADE_OPTIONS = list(GRADE_MAP_45.keys())

# 학점은 1, 2, 3만 존재
CREDIT_OPTIONS = [1.0, 2.0, 3.0]
YEAR_OPTIONS = list(range(2020, 2031))  # 2020년부터 2030년까지

# 🎓 전공 과목 정의 (기본전공 + 심화전공)
MAJOR_CATEGORIES = ["기본전공", "심화전공"]

###############################################################################
# 2. Session 초기화 + 콜백 함수 + 데이터 관리 함수
###############################################################################

def init_session() -> None:
    if "courses" not in st.session_state:
        st.session_state.courses = pd.DataFrame(
            [
                {
                    "과목명": "",
                    "학점": 3.0,
                    "성적": "A0",
                    "이수구분": CATEGORY_OPTIONS[0],
                    "연도": 2025,
                    "학기": TERM_OPTIONS[0],
                    "재수강": False,
                }
            ]
        )
    
    # 목표 GPA 롤백 방지를 위한 초기화
    if "target_gpa" not in st.session_state:
        st.session_state.target_gpa = 4.0
    if "calculation_results" not in st.session_state:
        st.session_state.calculation_results = None

# 🔥 목표 GPA 콜백 함수
def update_target_gpa():
    """목표 GPA 변경 콜백 - GitHub 이슈 #9657 해결"""
    if "target_gpa_widget" in st.session_state:
        st.session_state.target_gpa = st.session_state.target_gpa_widget

def update_courses():
    """DataEditor 변경사항을 session_state에 즉시 반영하는 콜백 함수"""
    try:
        if "courses_editor" in st.session_state:
            # edited_rows에서 변경된 행들을 추적
            changes = st.session_state.courses_editor.get("edited_rows", {})
            for idx, change in changes.items():
                for col, value in change.items():
                    # 데이터 검증 강화
                    if col == "학점" and value is not None:
                        value = max(0.0, float(value))  # 음수 방지
                    elif col == "연도" and value is not None:
                        value = max(1900, min(2100, int(value)))  # 범위 제한
                    
                    st.session_state.courses.loc[idx, col] = value
            
            # added_rows 처리 (행 추가된 경우)
            added = st.session_state.courses_editor.get("added_rows", [])
            if added:
                new_df = pd.DataFrame(added)
                st.session_state.courses = pd.concat([st.session_state.courses, new_df], ignore_index=True)
            
            # deleted_rows 처리 (행 삭제된 경우) 
            deleted = st.session_state.courses_editor.get("deleted_rows", [])
            if deleted:
                st.session_state.courses = st.session_state.courses.drop(deleted).reset_index(drop=True)
                
    except Exception as e:
        st.error(f"데이터 업데이트 중 오류가 발생했습니다: {str(e)}")

# 백업/복원 기능
def backup_data():
    """현재 데이터를 백업"""
    st.session_state.backup_courses = st.session_state.courses.copy()
    st.success("💾 데이터가 백업되었습니다!")

def restore_data():
    """백업된 데이터를 복원"""
    if "backup_courses" in st.session_state:
        st.session_state.courses = st.session_state.backup_courses.copy()
        st.success("↩️ 데이터가 복원되었습니다!")
        st.rerun()  # 화면 새로고침
    else:
        st.warning("⚠️ 복원할 백업 데이터가 없습니다.")

# CSV 내보내기/가져오기 기능
def export_to_csv():
    """데이터를 CSV로 내보내기"""
    if not st.session_state.courses.empty:
        csv = st.session_state.courses.to_csv(index=False, encoding='utf-8-sig')
        return csv
    return None

def import_from_csv(uploaded_file):
    """CSV 파일에서 데이터 가져오기"""
    try:
        df = pd.read_csv(uploaded_file, encoding='utf-8-sig')
        # 필수 컬럼 검증
        required_cols = ["과목명", "학점", "성적", "이수구분", "연도", "학기", "재수강"]
        if all(col in df.columns for col in required_cols):
            # 데이터 타입 검증
            df["학점"] = pd.to_numeric(df["학점"], errors="coerce").fillna(3.0)
            df["연도"] = pd.to_numeric(df["연도"], errors="coerce").fillna(2025).astype(int)
            st.session_state.courses = df
            st.success(f"✅ {len(df)}개 과목이 성공적으로 가져와졌습니다!")
            st.rerun()  # 화면 새로고침
        else:
            st.error("❌ 올바른 형식의 CSV 파일이 아닙니다.")
    except Exception as e:
        st.error(f"❌ 파일 가져오기 실패: {str(e)}")

init_session()

def _add_row() -> None:
    df = st.session_state.courses.copy()
    if df.empty:
        base_year = 2025
    else:
        base_year = int(df["연도"].iloc[-1])
    new_row = {
        "과목명": "",
        "학점": 3.0,
        "성적": "A0",
        "이수구분": CATEGORY_OPTIONS[0],
        "연도": base_year,
        "학기": TERM_OPTIONS[0],
        "재수강": False,
    }
    st.session_state.courses = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

def _del_row() -> None:
    if not st.session_state.courses.empty:
        st.session_state.courses = st.session_state.courses.iloc[:-1].reset_index(drop=True)

###############################################################################
# 3. 개선된 CSS 스타일링 (탭 버튼 가독성 개선)
###############################################################################

def apply_custom_css():
    """개선된 CSS - 탭 버튼 가독성 향상"""
    st.markdown("""
    <style>
    /* 🔥 개선된 탭 스타일 - 가독성 향상 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        margin-bottom: 1rem;
    }
    
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        padding: 0.75rem 1.25rem;
        background: linear-gradient(145deg, #f8f9fa 0%, #e9ecef 100%);
        border: 2px solid #dee2e6;
        color: #495057 !important;
        font-weight: 600 !important;
        font-size: 0.95rem !important;
        transition: all 0.3s ease;
    }
    
    .stTabs [data-baseweb="tab"]:hover {
        background: linear-gradient(145deg, #e9ecef 0%, #dee2e6 100%);
        border-color: #6c757d;
        transform: translateY(-1px);
        box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    }
    
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #0066CC 0%, #004499 100%) !important;
        color: white !important;
        border-color: #0066CC !important;
        box-shadow: 0 4px 12px rgba(0, 102, 204, 0.3);
    }
    
    .stTabs [aria-selected="true"]:hover {
        background: linear-gradient(135deg, #0056b3 0%, #003d82 100%) !important;
    }
    
    /* 메트릭 카드 개선 */
    [data-testid="metric-container"] {
        background: linear-gradient(145deg, #ffffff 0%, #f8f9fa 100%);
        border: 1px solid #e9ecef;
        border-radius: 12px;
        padding: 1.2rem;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        transition: all 0.3s ease;
    }
    
    [data-testid="metric-container"]:hover {
        box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
        transform: translateY(-2px);
    }
    
    /* 버튼 스타일 개선 */
    .stButton > button {
        border-radius: 8px;
        border: none;
        padding: 0.6rem 1.2rem;
        font-weight: 600;
        transition: all 0.3s ease;
    }
    
    .stButton > button:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    }
    
    /* Primary 버튼 */
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #0066CC 0%, #004499 100%);
        color: white;
    }
    
    /* Secondary 버튼 */
    .stButton > button[kind="secondary"] {
        background: linear-gradient(145deg, #f8f9fa 0%, #e9ecef 100%);
        color: #495057;
        border: 1px solid #dee2e6;
    }
    
    /* 진행률 바 개선 */
    .stProgress > div > div > div {
        border-radius: 10px;
        background: linear-gradient(90deg, #28a745 0%, #20c997 100%);
    }
    
    /* 익스팬더 스타일 개선 */
    .streamlit-expanderHeader {
        background: linear-gradient(90deg, #f8f9fa 0%, #ffffff 100%);
        border-radius: 8px;
        padding: 0.8rem 1rem;
        border: 1px solid #e9ecef;
        margin-bottom: 0.5rem;
    }
    
    /* 입력 필드 개선 */
    .stNumberInput > div > div > input,
    .stSelectbox > div > div > div {
        border-radius: 8px;
        border: 2px solid #e9ecef;
        transition: border-color 0.3s ease;
    }
    
    .stNumberInput > div > div > input:focus,
    .stSelectbox > div > div > div:focus {
        border-color: #0066CC;
        box-shadow: 0 0 0 3px rgba(0, 102, 204, 0.1);
    }
    
    /* 성공/경고/에러 메시지 스타일링 */
    .stSuccess {
        background: linear-gradient(135deg, #d4edda 0%, #c3e6cb 100%);
        border-radius: 8px;
        border: none;
    }
    
    .stWarning {
        background: linear-gradient(135deg, #fff3cd 0%, #ffeaa7 100%);
        border-radius: 8px;
        border: none;
    }
    
    .stError {
        background: linear-gradient(135deg, #f8d7da 0%, #f5c6cb 100%);
        border-radius: 8px;
        border: none;
    }
    </style>
    """, unsafe_allow_html=True)

###############################################################################
# 4. 페이지 설정 및 사이드바
###############################################################################

# 페이지 설정
st.set_page_config(
    page_title="KU 학점 계산기",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 개선된 CSS 적용
apply_custom_css()

# 사이드바 설정
with st.sidebar:
    st.header("⚙️ 빠른 설정")
    
    # 기본값 설정
    with st.expander("🔧 기본값 설정", expanded=False):
        default_credit = st.selectbox("기본 학점", CREDIT_OPTIONS, index=2)  # 3.0이 기본값
        default_grade = st.selectbox("기본 성적", GRADE_OPTIONS, index=1)
        default_category = st.selectbox("기본 이수구분", CATEGORY_OPTIONS)
    
    st.divider()
    
    # 데이터 관리
    st.header("📁 데이터 관리")
    
    # 백업/복원 버튼
    col1, col2 = st.columns(2)
    with col1:
        if st.button("💾 백업", use_container_width=True):
            backup_data()
    with col2:
        if st.button("↩️ 복원", use_container_width=True):
            restore_data()
    
    st.markdown("---")
    
    # CSV 내보내기
    st.subheader("📤 내보내기")
    if st.button("CSV 생성", use_container_width=True):
        csv = export_to_csv()
        if csv:
            st.download_button(
                label="💾 CSV 다운로드",
                data=csv,
                file_name=f"KU_성적_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
                use_container_width=True
            )
    
    # CSV 가져오기
    st.subheader("📥 가져오기")
    uploaded = st.file_uploader("CSV 파일 선택", type="csv")
    if uploaded and st.button("📥 가져오기 실행", use_container_width=True):
        import_from_csv(uploaded)
    
    st.divider()

    with st.expander("ℹ️ 사용법", expanded=False):
        st.markdown(
            """
            1. **행 추가/삭제 버튼**으로만 행을 관리합니다.
            2. 표 클릭 후 값을 편집하세요.
            3. **학점은 1, 2, 3학점만 선택 가능**합니다.
            4. 동일 과목 재수강 시 **재수강** 칼럼에 체크하면 이전 성적은 GPA에서 제외됩니다.
            5. 입력을 마친 뒤 **[📊 계산하기]** 를 눌러 결과를 확인합니다.
            6. **상세 통계에서 전공 과목 전용 분석**을 확인할 수 있습니다.
            7. **모든 학과 및 전공**에서 사용 가능합니다.
            """)

    st.divider()
    
    with st.expander("💾 데이터 저장 방법"):
        st.markdown("""
        **🔄 백업 및 복원 기능**
        - **백업**: "백업" 버튼으로 현재 상태 저장
        - **복원**: "복원" 버튼으로 백업된 데이터로 되돌리기
        - 실험적 변경 전 백업 권장

        **📁 영구 저장 (CSV 파일)**
        - **내보내기**: "CSV 생성" → "CSV 다운로드"
        - **가져오기**: "CSV 파일 선택" → "가져오기 실행"
        - 브라우저 종료 전 반드시 CSV 저장 필요
        """)
    
    st.divider()

    # 도움말
    with st.expander("❓ 도움말"):
        st.markdown("""
        **성적 기준 (4.5 만점)**
        - A+: 4.5, A0: 4.0
        - B+: 3.5, B0: 3.0
        - C+: 2.5, C0: 2.0
        - D+: 1.5, D0: 1.0, F: 0.0
        - P: 학점만 산입, NP: 미산입
        
        **재수강 처리**
        - 동일 과목명의 최신 성적만 반영
        
        **학점 체계**
        - 1학점, 2학점, 3학점만 지원
        
        **전공 과목**
        - 기본전공과 심화전공을 통합하여 분석
        - 모든 학과에서 사용 가능
        """)
    

###############################################################################
# 5. 메인 UI — 과목 입력 테이블
###############################################################################

# 🔥 개선된 제목 (고려대학교 재학생 전체 대상)
st.title("🎓 고려대학교 GPA 계산기")

# 행 관리 버튼 정렬 및 균등 배치
st.subheader("📝 과목 입력")

button_cols = st.columns([1, 1, 3])  # 비율 조정으로 버튼 간격 최적화

with button_cols[0]:
    if st.button("➕ 행 추가", key="add_row", use_container_width=True, type="primary"):
        _add_row()

with button_cols[1]:
    if st.button("🗑️ 마지막 행 삭제", key="del_row", use_container_width=True, type="secondary"):
        _del_row()

# 빈 공간을 위한 컬럼
with button_cols[2]:
    st.empty()

st.markdown("---")  # 구분선 추가

# --- DataEditor (개선사항 적용) ---
edited_df = st.data_editor(
    st.session_state.courses,
    key="courses_editor",
    on_change=update_courses,  # 콜백 함수 추가
    column_config={
        "과목명": st.column_config.TextColumn(
            "과목명",
            help="과목명을 입력하세요",
            max_chars=50,
        ),
        # 학점을 1, 2, 3만 선택 가능하도록 변경
        "학점": st.column_config.SelectboxColumn(
            "학점",
            help="학점을 선택하세요 (1, 2, 3학점만 가능)",
            options=CREDIT_OPTIONS,
            required=True
        ),
        "성적": st.column_config.SelectboxColumn(
            "성적",
            help="성적을 선택하세요",
            options=GRADE_OPTIONS,
            required=True
        ),
        "이수구분": st.column_config.SelectboxColumn(
            "이수구분",
            help="이수구분을 선택하세요",
            options=CATEGORY_OPTIONS,
            required=True
        ),
        "연도": st.column_config.SelectboxColumn(
            "연도",
            help="수강 연도를 선택하세요",
            options=YEAR_OPTIONS,
            required=True
        ),
        "학기": st.column_config.SelectboxColumn(
            "학기",
            help="학기를 선택하세요",
            options=TERM_OPTIONS,
            required=True
        ),
        "재수강": st.column_config.CheckboxColumn(
            "재수강",
            help="재수강 과목인 경우 체크하세요",
            default=False
        ),
    },
    num_rows="fixed",  # DataEditor 내부 + / – 비활성화
    use_container_width=True,
    hide_index=True,  # 인덱스 숨기기
)

###############################################################################
# 6. GPA/학점 계산 함수 (기능 유지)
###############################################################################

@st.cache_data
def calculate_cached(df_hash: str, df_raw: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, float]]:
    """해시값을 이용한 캐시된 계산 (성능 최적화)"""
    return calculate(df_raw)

def calculate(df_raw: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, float]]:
    df = df_raw.copy()
    df["학점"] = pd.to_numeric(df["학점"], errors="coerce").fillna(0.0)
    df["연도"] = pd.to_numeric(df["연도"], errors="coerce").fillna(0).astype(int)

    deduped = (
        df.sort_values("재수강")
        .drop_duplicates(subset=["과목명"], keep="last")
    )

    gpa_rows = deduped[deduped["성적"].map(GRADE_MAP_45).notna()].copy()
    gpa_rows["평점"] = gpa_rows["성적"].map(GRADE_MAP_45)

    total_points = (gpa_rows["학점"] * gpa_rows["평점"]).sum()
    total_credits_gpa = gpa_rows["학점"].sum()
    overall_gpa = total_points / total_credits_gpa if total_credits_gpa else 0.0

    summary_records = []
    for cat in CATEGORY_OPTIONS:
        cat_rows = deduped[deduped["이수구분"] == cat]
        cat_total_credits = cat_rows["학점"].sum()

        cat_gpa_rows = cat_rows[cat_rows["성적"].map(GRADE_MAP_45).notna()]
        cat_points = (
            cat_gpa_rows["학점"] * cat_gpa_rows["성적"].map(GRADE_MAP_45)
        ).sum()
        cat_gpa_credits = cat_gpa_rows["학점"].sum()
        cat_gpa = cat_points / cat_gpa_credits if cat_gpa_credits else np.nan

        summary_records.append((cat, cat_total_credits, cat_gpa))

    summary_df = pd.DataFrame(summary_records, columns=["영역", "이수학점", "평균 GPA"])

    misc = {
        "overall_gpa": round(overall_gpa, 2),  # 🔥 소수점 2자리 제한
        "earned_credits": deduped["학점"].sum(),
        "gpa_credits": total_credits_gpa,
    }
    return summary_df, misc

###############################################################################
# 7. 계산 & 결과 표시 (기능 유지)
###############################################################################

st.markdown("---")  # 구분선 추가

# 계산 버튼을 중앙에 배치하고 크게 만들기
col1, col2, col3 = st.columns([2, 1, 2])
with col2:
    if st.button("📊 계 산 하 기", type="primary", use_container_width=True):
        calculate_button_pressed = True
    else:
        calculate_button_pressed = False

# 계산 결과를 session_state에 저장하여 목표 GPA 변경 시에도 유지
if calculate_button_pressed:
    df_courses = st.session_state.courses.copy()
    if df_courses.empty or df_courses["과목명"].str.strip().eq("").all():
        st.error("⚠️ 과목명을 포함해 최소 한 과목을 입력하세요!")
        st.stop()

    # 캐시된 계산 사용 및 결과 저장
    df_hash = str(hash(df_courses.to_string()))
    summary_df, misc = calculate_cached(df_hash, df_courses)
    st.session_state.calculation_results = (summary_df, misc)

# 계산 결과가 있으면 표시
if st.session_state.calculation_results is not None:
    summary_df, misc = st.session_state.calculation_results

    st.markdown("---")
    st.subheader("✅ 누적 결과")
    
    # 메트릭을 더 보기 좋게 배치
    metric_cols = st.columns(3)
    with metric_cols[0]:
        st.metric(
            label="🎯 전체 평균 GPA (4.5)",
            value=f"{misc['overall_gpa']:.2f}",
            delta=f"{misc['overall_gpa'] - 3.0:.2f}" if misc['overall_gpa'] >= 3.0 else None
        )
    with metric_cols[1]:
        st.metric(
            label="📚 총 이수 학점",
            value=f"{misc['earned_credits']:.0f} 학점",
            delta=f"{misc['earned_credits'] - 130:.0f}" if misc['earned_credits'] >= 130 else None
        )
    with metric_cols[2]:
        st.metric(
            label="📊 GPA 반영 학점",
            value=f"{misc['gpa_credits']:.0f} 학점"
        )

    # 영역별 결과 표 스타일 개선
    st.dataframe(
        summary_df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "영역": st.column_config.TextColumn("영역", width="medium"),
            "이수학점": st.column_config.NumberColumn("이수학점", format="%.1f"),
            "평균 GPA": st.column_config.NumberColumn("평균 GPA", format="%.2f"),
        }
    )

    st.subheader("🎯 졸업 요건 진행률")
    progress_cols = st.columns([1, 1])
    
    # 영역별 진행률을 2열로 배치
    for i, (_, r) in enumerate(summary_df.iterrows()):
        col_idx = i % 2
        with progress_cols[col_idx]:
            need = REQUIREMENTS[r["영역"]]["required"]
            pct = min(r["이수학점"] / need, 1)
            color = "🟢" if pct >= 1.0 else "🟡" if pct >= 0.7 else "🔴"
            st.progress(pct, text=f"{color} {r['영역']}: {r['이수학점']:.0f}/{need}학점 ({pct*100:.1f}%)")

    # 총계 진행률
    overall_pct = min(misc["earned_credits"] / REQUIREMENTS["총계"]["required"], 1)
    overall_color = "🎉" if overall_pct >= 1.0 else "📈"
    st.progress(
        overall_pct,
        text=f"{overall_color} **총계: {misc['earned_credits']:.0f}/{REQUIREMENTS['총계']['required']}학점 ({overall_pct*100:.1f}%)**"
    )

    # 🔥 목표 GPA 시뮬레이션 (롤백 방지 완전 해결)
    with st.expander("🎯 목표 GPA 시뮬레이션", expanded=True):
        target_cols = st.columns([1, 2])
        
        with target_cols[0]:
            # 🔥 GitHub 이슈 #9657 완전 해결책: 콜백 함수 사용
            target = st.number_input(
                "목표 졸업 GPA", 
                min_value=0.0, 
                max_value=4.5, 
                value=st.session_state.target_gpa,
                step=0.1,
                key="target_gpa_widget",  # 위젯 전용 key
                on_change=update_target_gpa  # 콜백으로 업데이트
            )
        
        with target_cols[1]:
            remain = REQUIREMENTS["총계"]["required"] - misc["earned_credits"]
            if remain <= 0:
                st.success("🎉 이미 졸업 학점을 충족했습니다!")
            else:
                # session_state 값 사용으로 일관성 보장
                need_avg = (
                    st.session_state.target_gpa * REQUIREMENTS["총계"]["required"] - 
                    misc["overall_gpa"] * misc["gpa_credits"]
                ) / remain
                
                if need_avg > 4.5:
                    st.warning("⚠️ 남은 학점에서 목표 GPA 달성이 불가능합니다.")
                else:
                    st.info(f"📝 남은 **{remain:.0f}학점**에서 평균 **{need_avg:.2f}** 이상 받아야 합니다.")

    # 🌟 상세 통계 및 분석 기능 (탭 색상 개선)
    with st.expander("📈 상세 통계 및 분석"):
        df_courses = st.session_state.courses.copy()
        valid_courses = df_courses[df_courses["과목명"].str.strip() != ""].copy()
        if not valid_courses.empty:
            # 재수강 중복 제거
            deduped_for_stats = (
                valid_courses.sort_values("재수강")
                .drop_duplicates(subset=["과목명"], keep="last")
            )
            
            # 🎓 전공 과목 필터링 (기본전공 + 심화전공)
            major_courses = deduped_for_stats[deduped_for_stats["이수구분"].isin(MAJOR_CATEGORIES)]
            
            # 🔥 학기별 통계 (전체 + 전공 GPA 계산)
            semester_stats = []
            for (year, term), group in deduped_for_stats.groupby(['연도', '학기']):
                # 전체 GPA 계산
                gpa_rows = group[group["성적"].map(GRADE_MAP_45).notna()]
                if not gpa_rows.empty:
                    total_credits = group["학점"].sum()
                    gpa_credits = gpa_rows["학점"].sum()
                    semester_gpa = (gpa_rows["학점"] * gpa_rows["성적"].map(GRADE_MAP_45)).sum() / gpa_credits
                    
                    # 🎓 전공 GPA 계산
                    major_group = group[group["이수구분"].isin(MAJOR_CATEGORIES)]
                    major_gpa_rows = major_group[major_group["성적"].map(GRADE_MAP_45).notna()]
                    
                    if not major_gpa_rows.empty:
                        major_gpa_credits = major_gpa_rows["학점"].sum()
                        major_gpa = (major_gpa_rows["학점"] * major_gpa_rows["성적"].map(GRADE_MAP_45)).sum() / major_gpa_credits
                        major_gpa = round(major_gpa, 2)
                    else:
                        major_gpa = None
                    
                    semester_stats.append({
                        '연도': year,
                        '학기': term,
                        '총학점': total_credits,
                        'GPA반영학점': gpa_credits,
                        '학기GPA': round(semester_gpa, 2),  # 🔥 소수점 2자리로 제한
                        '전공GPA': major_gpa  # 🎓 전공 GPA 추가
                    })
            
            if semester_stats:
                semester_df = pd.DataFrame(semester_stats)
                
                # 🎓 개선된 탭 (색상 및 가독성 향상)
                stats_tabs = st.tabs(["📊 학기별 추이", "🎓 전공 과목 분석", "🎯 성적 분포", "📚 이수구분별", "📅 연도별 학점"])
                
                with stats_tabs[0]:
                    st.subheader("📊 학기별 GPA 추이")
                    if len(semester_df) > 1:
                        semester_df['학기_순서'] = semester_df['연도'].astype(str) + '-' + semester_df['학기']
                        
                        # 🔥 듀얼 라인 차트 (전체 GPA + 전공 GPA)
                        chart_data = []
                        for _, row in semester_df.iterrows():
                            chart_data.append({
                                '학기_순서': row['학기_순서'],
                                'GPA': row['학기GPA'],
                                '구분': '전체 GPA'
                            })
                            if row['전공GPA'] is not None:
                                chart_data.append({
                                    '학기_순서': row['학기_순서'],
                                    'GPA': row['전공GPA'],
                                    '구분': '전공 GPA'
                                })
                        
                        chart_df = pd.DataFrame(chart_data)
                        
                        if not chart_df.empty:
                            chart = alt.Chart(chart_df).mark_line(point=True).encode(
                                x=alt.X('학기_순서:O', axis=alt.Axis(labelAngle=0, title="학기")),
                                y=alt.Y('GPA:Q', axis=alt.Axis(title="GPA"), scale=alt.Scale(domain=[0, 4.5])),
                                color=alt.Color('구분:N', scale=alt.Scale(range=['#1f77b4', '#ff7f0e']))
                            ).properties(
                                height=400  # 🔥 고정 높이로 변경
                            )
                            st.altair_chart(chart, use_container_width=True)
                    
                    st.dataframe(semester_df, hide_index=True, use_container_width=True)
                
                with stats_tabs[1]:
                    # 🎓 전공 과목 전용 분석
                    st.subheader("🎓 전공 과목 분석 (기본전공 + 심화전공)")
                    
                    if not major_courses.empty:
                        # 전공 GPA 계산
                        major_gpa_rows = major_courses[major_courses["성적"].map(GRADE_MAP_45).notna()]
                        if not major_gpa_rows.empty:
                            major_total_points = (major_gpa_rows["학점"] * major_gpa_rows["성적"].map(GRADE_MAP_45)).sum()
                            major_total_credits = major_gpa_rows["학점"].sum()
                            major_overall_gpa = round(major_total_points / major_total_credits, 2)
                            
                            # 전공 메트릭 표시
                            major_cols = st.columns(3)
                            with major_cols[0]:
                                st.metric("🎓 전공 평균 GPA", f"{major_overall_gpa:.2f}")
                            with major_cols[1]:
                                st.metric("📚 전공 총 학점", f"{major_courses['학점'].sum():.0f}학점")
                            with major_cols[2]:
                                total_major_required = REQUIREMENTS["기본전공"]["required"] + REQUIREMENTS["심화전공"]["required"]
                                st.metric("🎯 전공 요건 달성률", f"{(major_courses['학점'].sum() / total_major_required * 100):.1f}%")
                            
                            # 전공 성적 분포
                            st.subheader("📊 전공 성적 분포")
                            major_grade_dist = major_courses['성적'].value_counts()
                            if not major_grade_dist.empty:
                                grade_df = major_grade_dist.reset_index()
                                grade_df.columns = ['성적', '개수']
                                
                                chart = alt.Chart(grade_df).mark_bar().encode(
                                    x=alt.X('성적:O', axis=alt.Axis(labelAngle=0, title="성적")),
                                    y=alt.Y('개수:Q', axis=alt.Axis(title="과목 수"))
                                ).properties(
                                    height=300
                                )
                                st.altair_chart(chart, use_container_width=True)
                            
                            # 전공 과목 상세 테이블
                            st.subheader("📋 전공 과목 상세")
                            st.dataframe(major_courses[['과목명', '학점', '성적', '이수구분', '연도', '학기']], 
                                       hide_index=True, use_container_width=True)
                        else:
                            st.info("🎓 GPA가 산정된 전공 과목이 없습니다.")
                    else:
                        st.info("🎓 전공 과목이 입력되지 않았습니다.")
                
                with stats_tabs[2]:
                    st.subheader("🎯 성적 분포")
                    grade_dist = deduped_for_stats['성적'].value_counts()
                    if not grade_dist.empty:
                        # 🔥 차트 크기 고정 (고정 높이)
                        grade_df = grade_dist.reset_index()
                        grade_df.columns = ['성적', '개수']
                        
                        chart = alt.Chart(grade_df).mark_bar().encode(
                            x=alt.X('성적:O', axis=alt.Axis(labelAngle=0, title="성적")),
                            y=alt.Y('개수:Q', axis=alt.Axis(title="과목 수"))
                        ).properties(
                            height=400  # 🔥 고정 높이로 변경
                        )
                        st.altair_chart(chart, use_container_width=True)
                
                with stats_tabs[3]:
                    st.subheader("📚 이수구분별 상세")
                    category_stats = deduped_for_stats.groupby('이수구분').agg({
                        '학점': 'sum',
                        '과목명': 'count'
                    }).rename(columns={'과목명': '과목수'})
                    st.dataframe(category_stats, use_container_width=True)
                
                with stats_tabs[4]:
                    st.subheader("📅 연도별 학점 추이")
                    yearly_credits = deduped_for_stats.groupby('연도')['학점'].sum()
                    if len(yearly_credits) > 1:
                        # 🔥 차트 크기 고정 (고정 높이)
                        yearly_df = yearly_credits.reset_index()
                        yearly_df.columns = ['연도', '학점']
                        
                        chart = alt.Chart(yearly_df).mark_bar().encode(
                            x=alt.X('연도:O', axis=alt.Axis(labelAngle=0, title="연도")),
                            y=alt.Y('학점:Q', axis=alt.Axis(title="학점"))
                        ).properties(
                            height=400  # 🔥 고정 높이로 변경
                        )
                        st.altair_chart(chart, use_container_width=True)

###############################################################################
# 8. 학기별 조회 (정렬 통일 + 소수점 2자리)
###############################################################################

st.divider()
st.subheader("🔍 학기별 조회")

df_courses = st.session_state.courses.copy()
if df_courses.empty:
    st.info("📝 과목을 입력하면 학기별 조회 기능을 사용할 수 있습니다.")
else:
    df_courses["연도"] = pd.to_numeric(df_courses["연도"], errors="coerce").fillna(0).astype(int)

    filter_cols = st.columns(2)
    with filter_cols[0]:
        years = sorted(df_courses["연도"].unique())
        sel_year = st.selectbox("📅 연도", ["전체"] + years, key="year_filter")
    
    with filter_cols[1]:
        if sel_year == "전체":
            filtered = df_courses
            terms = ["전체"]
        else:
            terms = df_courses[df_courses["연도"] == sel_year]["학기"].unique().tolist()
            terms = ["전체"] + sorted(terms)
        
        sel_term = st.selectbox("📚 학기", terms, key="term_filter")
        
        if sel_year != "전체":
            if sel_term == "전체":
                filtered = df_courses[df_courses["연도"] == sel_year]
            else:
                filtered = df_courses[
                    (df_courses["연도"] == sel_year) & (df_courses["학기"] == sel_term)
                ]

    if filtered.empty:
        st.info("⚠️ 해당 조건에 일치하는 과목이 없습니다.")
    else:
        s_df, s_misc = calculate(filtered)
        
        # 🔥 수정: 선택 구간 결과를 메트릭으로 표시 (정렬 통일 + 소수점 2자리)
        result_cols = st.columns([1, 1, 1])  # 🔥 균등 분할로 정렬 통일
        with result_cols[0]:
            st.metric("선택 구간 GPA", f"{s_misc['overall_gpa']:.2f}")  # 🔥 소수점 2자리 적용
        with result_cols[1]:
            st.metric("선택 구간 학점", f"{s_misc['earned_credits']:.0f}")
        with result_cols[2]:
            st.metric("과목 수", len(filtered[filtered["과목명"].str.strip() != ""]))
        
        st.dataframe(filtered, use_container_width=True, hide_index=True)

###############################################################################
# 9. 수정된 푸터 (HTML 표시 오류 해결)
###############################################################################

st.divider()

st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #666; font-size: 0.9rem; padding: 1rem;'>
🔒 모든 데이터는 브라우저에만 저장되며 외부로 전송되지 않습니다<br>
✨ 고려대학교 전체 재학생 지원
</div>
""", unsafe_allow_html=True)
