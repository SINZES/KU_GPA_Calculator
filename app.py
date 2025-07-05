"""
고려대학교 재학생을 위한 학점·GPA 계산기  —  Streamlit
────────────────────────────────────────────────────────
* Streamlit ≥1.25, pandas ≥2.0 필요
* 실행:  `streamlit run app.py`
* 2025‑07‑05  — v2.5 final.
"""

from __future__ import annotations

from typing import Dict, Tuple, List, Optional
import io
import re
import json
import base64

import numpy as np
import pandas as pd
import streamlit as st
import altair as alt

###############################################################################
# 1. 기준 데이터 — 성적 등급 매핑 & 새로운 졸업 요건
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

# 🔥 새로운 기본 졸업 요건 (학문의기초 제거)
DEFAULT_REQUIREMENTS: Dict[str, Dict[str, int]] = {
    "공통교양": {"required": 13},
    "핵심교양": {"required": 6},
    "전공필수": {"required": 18},
    "전공선택": {"required": 24},
    "심화전공": {"required": 30},  # 전공선택 초과분으로 자동 계산
    "일반선택": {"required": 39},
    "총계": {"required": 130},
}

# 🎓 전공 유형 옵션 (mutually exclusive)
MAJOR_TYPE_OPTIONS = ["심화전공", "복수전공", "이중전공"]

# 🏷️ 새로운 기본 이수구분 (학문의기초 제거)
BASE_CATEGORIES = ["공통교양", "핵심교양", "전공필수", "전공선택", "일반선택"]

# 🔥 미리 정의된 추가 이수구분 옵션
PREDEFINED_CATEGORIES = [
    "학문의기초", "전공관련교양", "경제학필수과목", "교직", "실용영어", 
    "글로벌커뮤니케이션", "창의적사고", "리더십개발", "인성교육"
]

TERM_OPTIONS = ["1학기", "2학기", "여름", "겨울"]
GRADE_OPTIONS = list(GRADE_MAP_45.keys())

# 🔥 학점은 3, 2, 1 순서로 (역순)
CREDIT_OPTIONS = [3.0, 2.0, 1.0]

# 🔥 연도는 현재연도 ±10년, 역순 (2025가 상단에)
def get_year_options():
    """현재 연도 기준 ±10년, 역순으로 반환"""
    current_year = pd.Timestamp.now().year
    start_year = current_year + 10  # 2035
    end_year = current_year - 10    # 2015
    return list(range(start_year, end_year - 1, -1))  # 역순

YEAR_OPTIONS = get_year_options()  # 동적으로 생성

# 🎨 통일된 차트 색상 팔레트
CHART_COLORS = {
    'primary': '#0066CC',
    'secondary': '#FF6B35', 
    'success': '#28a745',
    'warning': '#ffc107',
    'danger': '#dc3545',
    'info': '#17a2b8',
    'light': '#f8f9fa',
    'dark': '#343a40'
}

###############################################################################
# 2. 핵심 함수들 우선 정의 (NameError 해결)
###############################################################################

def get_categories_by_major_type(major_type: str) -> List[str]:
    """전공 유형에 따른 이수구분 목록 반환 (심화전공은 과목 입력에서 제외)"""
    categories = BASE_CATEGORIES.copy()
    
    if major_type == "복수전공":
        categories.extend(["복수전공 필수", "복수전공 선택"])
    elif major_type == "이중전공":
        categories.extend(["이중전공 필수", "이중전공 선택"])
    # 심화전공은 과목 입력 카테고리에 포함하지 않음
    
    return categories

def get_requirements_categories_by_major_type(major_type: str) -> List[str]:
    """졸업 요건 설정용 카테고리 반환 (심화전공 포함)"""
    categories = BASE_CATEGORIES.copy()
    
    if major_type == "심화전공":
        categories.append("심화전공")  # 심화전공 추가
    elif major_type == "복수전공":
        categories.extend(["복수전공 필수", "복수전공 선택"])
    elif major_type == "이중전공":
        categories.extend(["이중전공 필수", "이중전공 선택"])
    
    return categories

def get_requirements_by_major_type(major_type: str) -> Dict[str, Dict[str, int]]:
    """전공 유형에 따른 졸업 요건 반환"""
    requirements = DEFAULT_REQUIREMENTS.copy()
    
    if major_type == "복수전공":
        requirements["복수전공 필수"] = {"required": 18}
        requirements["복수전공 선택"] = {"required": 36}
        # 심화전공 제거 (복수전공 시에는 심화전공 없음)
        if "심화전공" in requirements:
            del requirements["심화전공"]
    elif major_type == "이중전공":
        requirements["이중전공 필수"] = {"required": 18}
        requirements["이중전공 선택"] = {"required": 24}
        # 심화전공 제거 (이중전공 시에는 심화전공 없음)
        if "심화전공" in requirements:
            del requirements["심화전공"]
    
    return requirements

def auto_calculate_total_credits(requirements):
    """영역별 요구학점 합으로 총 졸업학점 자동 계산"""
    total_credits = 0
    for category, req in requirements.items():
        if category != "총계":  # 총계는 제외
            total_credits += req["required"]
    
    return total_credits

def update_requirement_with_auto_total(category):
    """특정 카테고리 요구 학점 업데이트 + 총 졸업학점 자동 계산"""
    def callback():
        widget_key = f"req_{category}_widget"
        if widget_key in st.session_state:
            # 개별 영역 요구학점 업데이트
            st.session_state.custom_requirements[category]["required"] = st.session_state[widget_key]
            
            # 총 졸업학점 자동 계산
            auto_total = auto_calculate_total_credits(st.session_state.custom_requirements)
            st.session_state.custom_requirements["총계"]["required"] = auto_total
            
            # 캐시 무효화
            invalidate_cache()
            
    return callback

def validate_graduation_requirements_fixed():
    """졸업요건 논리적 일치성 검증 - 수정된 버전"""
    requirements = st.session_state.custom_requirements
    issues_found = []
    fixes_applied = []
    
    # 총 졸업학점을 각 영역 합으로 자동 계산
    auto_total = auto_calculate_total_credits(requirements)
    current_total = requirements.get("총계", {"required": 130})["required"]
    
    if current_total != auto_total:
        issues_found.append(f"총 졸업학점({current_total})이 영역별 합계({auto_total})와 일치하지 않습니다")
        st.session_state.custom_requirements["총계"]["required"] = auto_total
        fixes_applied.append(f"총 졸업학점을 {auto_total}학점으로 자동 수정했습니다")
    
    # 음수 학점 검증
    for cat, req in requirements.items():
        if req["required"] < 0:
            issues_found.append(f"'{cat}' 요구 학점이 음수입니다")
            st.session_state.custom_requirements[cat]["required"] = 0
            fixes_applied.append(f"'{cat}' 요구 학점을 0으로 수정했습니다")
    
    # 비현실적으로 높은 학점 검증 (각 영역당 100학점 초과)
    for cat, req in requirements.items():
        if cat != "총계" and req["required"] > 100:
            issues_found.append(f"'{cat}' 요구 학점({req['required']})이 비현실적으로 높습니다")
            st.session_state.custom_requirements[cat]["required"] = 30
            fixes_applied.append(f"'{cat}' 요구 학점을 30학점으로 조정했습니다")
    
    return issues_found, fixes_applied


def get_current_requirements():
    """현재 설정된 졸업 요건 반환 (커스텀 이수구분 포함)"""
    if "custom_requirements" not in st.session_state:
        return DEFAULT_REQUIREMENTS.copy()
    return st.session_state.custom_requirements

def get_current_categories():
    """현재 설정된 이수구분 목록 반환 (과목 입력용, 커스텀 이수구분 포함)"""
    if "custom_categories" not in st.session_state or "major_type" not in st.session_state:
        return BASE_CATEGORIES.copy()
    
    # 기본 카테고리 + 커스텀 추가 카테고리
    base_categories = get_categories_by_major_type(st.session_state.major_type)
    
    # session_state에 저장된 추가 카테고리들도 포함
    additional_categories = []
    for cat in st.session_state.custom_categories:
        if cat not in base_categories:
            additional_categories.append(cat)
    
    return base_categories + additional_categories

def get_major_categories():
    """🔥 동적 전공 과목 카테고리 반환"""
    if "major_type" not in st.session_state:
        return ["전공필수", "전공선택"]
    
    major_type = st.session_state.major_type
    major_cats = ["전공필수", "전공선택"]
    
    if major_type == "복수전공":
        major_cats.extend(["복수전공 필수", "복수전공 선택"])
    elif major_type == "이중전공":
        major_cats.extend(["이중전공 필수", "이중전공 선택"])
    
    return major_cats

###############################################################################
# 2-1. 성능 최적화 함수들
###############################################################################

@st.cache_data(ttl=60)  # 1분간 캐시
def get_cached_categories():
    """캐시된 이수구분 목록 반환 (성능 최적화)"""
    try:
        return get_current_categories()
    except Exception as e:
        st.error(f"이수구분 목록 로드 실패: {e}")
        return BASE_CATEGORIES.copy()

@st.cache_data(ttl=300)  # 5분간 캐시
def get_cached_requirements():
    """졸업 요건 반환 (성능 최적화)"""
    try:
        return get_current_requirements()
    except Exception as e:
        st.error(f"졸업 요건 로드 실패: {e}")
        return DEFAULT_REQUIREMENTS.copy()

def invalidate_cache():
    """캐시 무효화 함수"""
    get_cached_categories.clear()
    get_cached_requirements.clear()

###############################################################################
# 2-2. 강화된 데이터 검증 함수들
###############################################################################

def validate_course_data(df):
    """과목 데이터 유효성 검증 - 강화된 예외 처리"""
    issues = []
    fixes = []
    
    try:
        if df.empty:
            return issues, fixes
        
        # 1. 빈 과목명 검증 (개선된 예외 처리)
        try:
            # NaN 값을 먼저 처리
            df["과목명"] = df["과목명"].fillna("")
            
            # string으로 변환 후 빈 값 검증
            if df["과목명"].dtype == 'object':
                df["과목명"] = df["과목명"].astype(str)
            
            empty_names = df["과목명"].str.strip() == ""
            if empty_names.any():
                count = empty_names.sum()
                issues.append(f"빈 과목명이 {count}개 있습니다")
                # 자동 수정: 인덱스 기반 과목명 생성
                for idx in df[empty_names].index:
                    df.loc[idx, "과목명"] = f"과목{idx + 1}"
                fixes.append(f"빈 과목명을 자동 생성했습니다 ({count}개)")
                
        except AttributeError:
            # .str accessor를 사용할 수 없는 경우 (Non-string 데이터)
            issues.append("과목명 데이터 형식에 문제가 있습니다")
            df["과목명"] = df["과목명"].fillna("").astype(str)
            fixes.append("과목명을 문자열로 변환했습니다")
        except TypeError as e:
            issues.append(f"과목명 데이터 타입 오류: {str(e)}")
            df["과목명"] = "과목명"
            fixes.append("과목명을 기본값으로 설정했습니다")
        
        # 2. 학점 유효성 검증 (개선된 예외 처리)
        try:
            # 숫자형 변환 시도
            df["학점"] = pd.to_numeric(df["학점"], errors='coerce')
            
            # NaN 값 처리
            nan_credits = df["학점"].isna()
            if nan_credits.any():
                count = nan_credits.sum()
                issues.append(f"숫자가 아닌 학점이 {count}개 있습니다")
                df.loc[nan_credits, "학점"] = 3.0
                fixes.append(f"잘못된 학점을 3.0으로 수정했습니다 ({count}개)")
            
            # 유효하지 않은 학점 검증
            invalid_credits = ~df["학점"].isin(CREDIT_OPTIONS)
            if invalid_credits.any():
                count = invalid_credits.sum()
                issues.append(f"허용되지 않은 학점이 {count}개 있습니다")
                df.loc[invalid_credits, "학점"] = 3.0
                fixes.append(f"허용되지 않은 학점을 3.0으로 수정했습니다 ({count}개)")
                
        except (KeyError, TypeError, ValueError) as e:
            issues.append(f"학점 데이터에 심각한 문제가 있습니다: {str(e)}")
            if "학점" not in df.columns:
                df["학점"] = 3.0
                fixes.append("학점 컬럼을 3.0으로 생성했습니다")
            else:
                df["학점"] = df["학점"].fillna(3.0)
                fixes.append("학점 데이터를 기본값으로 복구했습니다")
        
        # 3. 성적 유효성 검증 (개선된 예외 처리)
        try:
            # 문자열로 변환
            df["성적"] = df["성적"].fillna("").astype(str)
            
            invalid_grades = ~df["성적"].isin(GRADE_OPTIONS)
            if invalid_grades.any():
                count = invalid_grades.sum()
                issues.append(f"올바르지 않은 성적이 {count}개 있습니다")
                df.loc[invalid_grades, "성적"] = "A0"
                fixes.append(f"올바르지 않은 성적을 A0으로 수정했습니다 ({count}개)")
                
        except (KeyError, TypeError) as e:
            issues.append(f"성적 데이터 처리 오류: {str(e)}")
            if "성적" not in df.columns:
                df["성적"] = "A0"
                fixes.append("성적 컬럼을 A0으로 생성했습니다")
            else:
                df["성적"] = "A0"
                fixes.append("성적 데이터를 기본값으로 복구했습니다")
        
        # 4. 연도 유효성 검증 (개선된 예외 처리)
        try:
            current_year = pd.Timestamp.now().year
            
            # 숫자형 변환
            df["연도"] = pd.to_numeric(df["연도"], errors='coerce')
            
            # NaN 값 처리
            nan_years = df["연도"].isna()
            if nan_years.any():
                count = nan_years.sum()
                issues.append(f"숫자가 아닌 연도가 {count}개 있습니다")
                df.loc[nan_years, "연도"] = current_year
                fixes.append(f"잘못된 연도를 {current_year}로 수정했습니다 ({count}개)")
            
            # 범위 검증 (2000-2030)
            invalid_years = (df["연도"] < 2000) | (df["연도"] > current_year + 5)
            if invalid_years.any():
                count = invalid_years.sum()
                issues.append(f"비현실적인 연도가 {count}개 있습니다")
                df.loc[invalid_years, "연도"] = current_year
                fixes.append(f"비현실적인 연도를 {current_year}로 수정했습니다 ({count}개)")
                
        except Exception as e:
            issues.append(f"연도 데이터 처리 중 오류: {str(e)}")
            df["연도"] = pd.Timestamp.now().year
            fixes.append("연도를 현재 연도로 설정했습니다")
        
        # 5. 이수구분 유효성 검증 (개선된 예외 처리)
        try:
            valid_categories = get_current_categories()
            if not valid_categories:
                issues.append("유효한 이수구분 목록을 불러올 수 없습니다")
                valid_categories = BASE_CATEGORIES.copy()
                fixes.append("기본 이수구분 목록을 사용합니다")
            
            df["이수구분"] = df["이수구분"].fillna("").astype(str)
            invalid_categories = ~df["이수구분"].isin(valid_categories)
            
            if invalid_categories.any():
                count = invalid_categories.sum()
                issues.append(f"존재하지 않는 이수구분이 {count}개 있습니다")
                default_category = valid_categories[0] if valid_categories else "공통교양"
                df.loc[invalid_categories, "이수구분"] = default_category
                fixes.append(f"존재하지 않는 이수구분을 '{default_category}'로 수정했습니다 ({count}개)")
                
        except Exception as e:
            issues.append(f"이수구분 검증 중 오류: {str(e)}")
            df["이수구분"] = "공통교양"
            fixes.append("이수구분을 기본값으로 설정했습니다")
        
        # 6. 학기 유효성 검증 추가
        try:
            df["학기"] = df["학기"].fillna("").astype(str)
            invalid_terms = ~df["학기"].isin(TERM_OPTIONS)
            
            if invalid_terms.any():
                count = invalid_terms.sum()
                issues.append(f"올바르지 않은 학기가 {count}개 있습니다")
                df.loc[invalid_terms, "학기"] = TERM_OPTIONS[0]
                fixes.append(f"올바르지 않은 학기를 '{TERM_OPTIONS[0]}'로 수정했습니다 ({count}개)")
                
        except Exception as e:
            issues.append(f"학기 검증 중 오류: {str(e)}")
            df["학기"] = TERM_OPTIONS[0]
            fixes.append("학기를 기본값으로 설정했습니다")
        
        # 7. 재수강 컬럼 검증 추가
        try:
            # Boolean 타입으로 변환
            df["재수강"] = df["재수강"].fillna(False).astype(bool)
            
        except Exception as e:
            issues.append(f"재수강 데이터 처리 중 오류: {str(e)}")
            df["재수강"] = False
            fixes.append("재수강 데이터를 기본값(False)으로 설정했습니다")
        
    except Exception as e:
        issues.append(f"데이터 검증 중 예상치 못한 오류: {str(e)}")
        fixes.append("기본값으로 복구를 시도했습니다")
    
    return issues, fixes


def get_valid_courses(df):
    """유효한 과목만 필터링"""
    if df.empty:
        return df
    
    # 기본 조건: 과목명이 있고, 학점이 0보다 크고, 성적이 유효한 경우
    valid_mask = (
        df["과목명"].notna() & 
        (df["과목명"].str.strip() != "") &
        (df["학점"] > 0) &
        df["성적"].isin(GRADE_OPTIONS) &
        df["이수구분"].isin(get_current_categories())
    )
    
    return df[valid_mask].copy()

def show_data_quality_report(df):
    """데이터 품질 보고서 표시"""
    if df.empty:
        return
    
    total_courses = len(df)
    valid_courses = get_valid_courses(df)
    valid_count = len(valid_courses)
    
    if valid_count < total_courses:
        st.warning(f"⚠️ **데이터 품질 경고**: 전체 {total_courses}개 과목 중 {valid_count}개만 유효합니다")
        
        with st.expander("📊 데이터 품질 상세 보고서"):
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown("**문제가 있는 데이터:**")
                
                # 빈 과목명
                empty_names = df["과목명"].isna() | (df["과목명"].str.strip() == "")
                if empty_names.any():
                    st.error(f"• 빈 과목명: {empty_names.sum()}개")
                
                # 잘못된 학점
                invalid_credits = ~df["학점"].isin(CREDIT_OPTIONS)
                if invalid_credits.any():
                    st.error(f"• 잘못된 학점: {invalid_credits.sum()}개")
                
                # 잘못된 성적
                invalid_grades = ~df["성적"].isin(GRADE_OPTIONS)
                if invalid_grades.any():
                    st.error(f"• 잘못된 성적: {invalid_grades.sum()}개")
            
            with col2:
                st.markdown("**통계:**")
                st.info(f"• 유효한 과목: {valid_count}개")
                st.info(f"• 전체 학점: {df['학점'].sum():.1f}학점")
                st.info(f"• 유효 학점: {valid_courses['학점'].sum():.1f}학점")
    else:
        st.success(f"✅ **데이터 품질 양호**: 전체 {total_courses}개 과목이 모두 유효합니다")

def safe_filter_courses(df):
    """안전한 과목 필터링 함수 - .str accessor 오류 방지"""
    if df.empty:
        return pd.DataFrame()
    
    try:
        if "과목명" in df.columns:
            # NaN 값을 빈 문자열로 처리
            df = df.copy()
            df["과목명"] = df["과목명"].fillna("").astype(str)
            return df[df["과목명"].str.strip() != ""]
        else:
            return pd.DataFrame()
    except Exception as e:
        st.warning(f"과목 필터링 중 오류: {str(e)}")
        return pd.DataFrame()
    
def validate_retake_status(df):
    """재수강 상태 검증 및 자동 수정"""
    if df.empty:
        return df
    
    # 빈 과목명인 행의 재수강 체크박스를 False로 설정
    empty_course_mask = (
        df["과목명"].isna() | 
        (df["과목명"].astype(str).str.strip() == "")
    )
    
    # 빈 과목명인 행의 재수강을 False로 강제 설정
    if empty_course_mask.any():
        df.loc[empty_course_mask, "재수강"] = False
    
    return df
    
def safe_data_operation(operation_func, error_message="작업 중 오류가 발생했습니다"):
    """안전한 데이터 작업 래퍼 함수 - 구체적인 예외 처리"""
    try:
        return operation_func()
    except pd.errors.EmptyDataError:
        st.error(f"⚠️ {error_message}: 데이터가 비어있습니다.")
        st.info("💡 **해결 방법**: 과목을 추가한 후 다시 시도하세요.")
        return None
    except pd.errors.ParserError as e:
        st.error(f"⚠️ {error_message}: 데이터 파싱 오류 - {str(e)}")
        st.info("💡 **해결 방법**: CSV 파일 형식을 확인하고 다시 가져오세요.")
        return None
    except KeyError as e:
        st.error(f"⚠️ {error_message}: 필수 컬럼이 없습니다 - {str(e)}")
        st.info("💡 **해결 방법**: 필수 컬럼(과목명, 학점, 성적 등)을 확인하세요.")
        return None
    except ValueError as e:
        st.error(f"⚠️ {error_message}: 데이터 형식 오류 - {str(e)}")
        st.info("💡 **해결 방법**: 학점은 숫자, 성적은 올바른 등급을 입력하세요.")
        return None
    except MemoryError:
        st.error(f"⚠️ {error_message}: 메모리 부족입니다.")
        st.info("💡 **해결 방법**: 데이터 크기를 줄이거나 브라우저를 재시작하세요.")
        return None
    except Exception as e:
        st.error(f"⚠️ {error_message}: 예상치 못한 오류 - {str(e)}")
        st.info("💡 **해결 방법**: 페이지를 새로고침하거나 백업 데이터를 복원해보세요.")
        return None

def safe_calculate_with_enhanced_error_handling(courses_df):
    """강화된 예외 처리를 포함한 안전한 계산"""
    try:
        if courses_df.empty:
            return {
                'success': True,
                'gpa': 0.0,
                'total_credits': 0.0,
                'warnings': [],
                'errors': []
            }
        
        # 데이터 타입 검증
        required_columns = ['과목명', '학점', '성적', '이수구분', '연도', '학기', '재수강']
        missing_columns = [col for col in required_columns if col not in courses_df.columns]
        
        if missing_columns:
            return {
                'success': False,
                'error': f"필수 컬럼이 누락되었습니다: {', '.join(missing_columns)}",
                'gpa': 0.0,
                'total_credits': 0.0
            }
        
        # 여기서 실제 GPA 계산 로직 수행
        # (이전에 제시한 process_retake_courses_correct 함수 사용)
        
        return {
            'success': True,
            'gpa': 0.0,  # 실제 계산 결과로 교체
            'total_credits': 0.0,  # 실제 계산 결과로 교체
            'warnings': [],
            'errors': []
        }
        
    except pd.errors.EmptyDataError:
        return {
            'success': False,
            'error': "데이터가 비어있습니다",
            'gpa': 0.0,
            'total_credits': 0.0
        }
    except KeyError as e:
        return {
            'success': False,
            'error': f"필수 데이터가 없습니다: {str(e)}",
            'gpa': 0.0,
            'total_credits': 0.0
        }
    except ValueError as e:
        return {
            'success': False,
            'error': f"데이터 형식 오류: {str(e)}",
            'gpa': 0.0,
            'total_credits': 0.0
        }
    except Exception as e:
        return {
            'success': False,
            'error': f"예상치 못한 오류가 발생했습니다: {str(e)}",
            'gpa': 0.0,
            'total_credits': 0.0
        }

###############################################################################
# 2-3. Session 초기화 + 콜백 함수 + 데이터 관리 함수
###############################################################################

def validate_category_name(name: str) -> Tuple[bool, str]:
    """이수구분 이름 검증"""
    if not name or not name.strip():
        return False, "이수구분 이름을 입력하세요"
    
    name = name.strip()
    
    # 길이 제한 (20자)
    if len(name) > 20:
        return False, "이수구분 이름은 20자 이내로 입력하세요"
    
    # 특수문자 제한 (한글, 영문, 숫자, 공백만 허용)
    if not re.match(r'^[가-힣a-zA-Z0-9\s]+$', name):
        return False, "한글, 영문, 숫자, 공백만 사용 가능합니다"
    
    # 중복 방지
    current_categories = get_current_categories()
    if name in current_categories:
        return False, "이미 존재하는 이수구분입니다"
    
    # 기본 카테고리와 중복 방지
    all_base_categories = BASE_CATEGORIES + ["심화전공", "복수전공 필수", "복수전공 선택", "이중전공 필수", "이중전공 선택"]
    if name in all_base_categories:
        return False, "기본 이수구분과 중복됩니다"
    
    return True, ""

def migrate_existing_data():
    """기존 사용자 데이터 마이그레이션 (학문의기초 자동 추가)"""
    if "courses" in st.session_state and not st.session_state.courses.empty:
        # 기존 과목 중에 학문의기초가 있는지 확인
        existing_categories = st.session_state.courses["이수구분"].unique()
        if "학문의기초" in existing_categories:
            # 학문의기초가 있으면 커스텀 카테고리에 추가
            if "학문의기초" not in st.session_state.custom_categories:
                st.session_state.custom_categories.append("학문의기초")
            
            # 졸업 요건에도 추가 (기본값 12학점)
            if "학문의기초" not in st.session_state.custom_requirements:
                st.session_state.custom_requirements["학문의기초"] = {"required": 12}

def init_session() -> None:
    # 🔥 커스텀 졸업 요건 초기화
    if "custom_requirements" not in st.session_state:
        st.session_state.custom_requirements = DEFAULT_REQUIREMENTS.copy()
    
    # 🔥 전공 유형 초기화
    if "major_type" not in st.session_state:
        st.session_state.major_type = "심화전공"
    
    # 🔥 커스텀 이수구분 초기화
    if "custom_categories" not in st.session_state:
        st.session_state.custom_categories = get_categories_by_major_type(st.session_state.major_type)
    
    if "courses" not in st.session_state:
        # 안전한 기본 이수구분 설정
        default_categories = get_current_categories()
        default_category = default_categories[0] if default_categories else "공통교양"
        
        current_year = pd.Timestamp.now().year

        st.session_state.courses = pd.DataFrame(
            [
                {
                    "과목명": "",
                    "학점": 3.0,
                    "성적": "A0",
                    "이수구분": default_category,
                    "연도": current_year,  # 현재 연도 (2025)
                    "학기": TERM_OPTIONS[0],
                    "재수강": False,
                }
            ]
        )
    
    # 🔥 기존 데이터 마이그레이션
    migrate_existing_data()
    
    # 목표 GPA 롤백 방지를 위한 초기화
    if "target_gpa" not in st.session_state:
        st.session_state.target_gpa = 4.0
    if "calculation_results" not in st.session_state:
        st.session_state.calculation_results = None

    # 🔥 여기에 추가하세요 - 전공 유형 변경 플래그 초기화 (새로 추가)
    if "major_type_changing" not in st.session_state:
        st.session_state.major_type_changing = False

# 🔥 커스텀 이수구분 관리 함수들
def add_custom_category(category_name: str):
    """커스텀 이수구분 추가"""
    is_valid, error_msg = validate_category_name(category_name)
    if not is_valid:
        st.error(f"⚠️ {error_msg}")
        return False
    
    # 이수구분 추가
    st.session_state.custom_categories.append(category_name.strip())
    
    # 졸업 요건에도 추가 (기본값 0학점)
    st.session_state.custom_requirements[category_name.strip()] = {"required": 0}
    
    # 🔥 캐시 무효화
    invalidate_cache()
    
    st.success(f"✅ '{category_name.strip()}' 이수구분이 추가되었습니다!")
    return True

def remove_custom_category(category_name: str):
    """커스텀 이수구분 삭제"""
    # 기본 카테고리는 삭제 불가
    base_categories = get_categories_by_major_type(st.session_state.major_type)
    if category_name in base_categories:
        st.error("⚠️ 기본 이수구분은 삭제할 수 없습니다!")
        return False
    
    # 해당 이수구분을 사용하는 과목이 있는지 확인
    if not st.session_state.courses.empty:
        using_courses = st.session_state.courses[st.session_state.courses["이수구분"] == category_name]
        if not using_courses.empty:
            st.error(f"⚠️ '{category_name}' 이수구분을 사용하는 과목이 {len(using_courses)}개 있습니다. 먼저 해당 과목들의 이수구분을 변경하세요!")
            return False
    
    # 이수구분 삭제
    if category_name in st.session_state.custom_categories:
        st.session_state.custom_categories.remove(category_name)
    
    # 졸업 요건에서도 삭제
    if category_name in st.session_state.custom_requirements:
        del st.session_state.custom_requirements[category_name]
    
    # 🔥 캐시 무효화
    invalidate_cache()
    
    st.success(f"✅ '{category_name}' 이수구분이 삭제되었습니다!")
    return True

# 🔥 진행률 색상 함수
def get_progress_color(pct: float) -> str:
    """진행률에 따른 색상 반환"""
    if pct >= 1.0:  # 100% 이상
        return CHART_COLORS['success']  # 초록색 (성공)
    elif pct >= 0.7:  # 70-99%
        return CHART_COLORS['warning']  # 노란색 (주의)
    else:  # 70% 미만
        return CHART_COLORS['danger']  # 빨간색 (경고)

# 🔥 DataEditor 동적 높이 계산 함수
def calculate_data_editor_height(df):
    """과목 수에 따른 data_editor 무제한 동적 높이 계산"""
    base_height = 35  # 행당 기본 높이 (픽셀)
    header_height = 40  # 헤더 높이
    min_height = 150  # 최소 높이 (빈 테이블도 보이도록)
    padding = 20  # 여백
    
    # 🔥 최대 높이 제한 제거 - 무제한 확장
    calculated_height = (len(df) * base_height) + header_height + padding
    
    # 최소값만 제한 (최대값 제한 없음)
    return max(min_height, calculated_height)

def update_requirement(category):
    """특정 카테고리 요구 학점 업데이트 콜백"""
    def callback():
        widget_key = f"req_{category}_widget"
        if widget_key in st.session_state:
            st.session_state.custom_requirements[category]["required"] = st.session_state[widget_key]
    return callback

# 🔥 목표 GPA 콜백 함수
def update_target_gpa():
    """목표 GPA 변경 콜백 - GitHub 이슈 #9657 해결"""
    if "target_gpa_widget" in st.session_state:
        st.session_state.target_gpa = st.session_state.target_gpa_widget

def protect_expander_state():
    """체크박스 변경 시 expander 상태 보호"""
    st.session_state.stats_expanded = True  # 강제로 열린 상태 유지

# 🔥 에러 처리 개선
def safe_execute(func, error_message="오류가 발생했습니다", success_message=None):
    """안전한 함수 실행 with 개선된 에러 처리"""
    try:
        result = func()
        if success_message:
            st.success(success_message)
        return result
    except Exception as e:
        st.error(f"⚠️ {error_message}: {str(e)}")
        st.info("💡 **해결 방법**: 페이지를 새로고침하거나 백업 데이터를 복원해보세요.")
        return None

def update_courses():
    """DataEditor 변경사항을 session_state에 즉시 반영하는 콜백 함수"""
    def _update():
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
            
            # ✅ 재수강 상태 검증 및 자동 수정 추가
            st.session_state.courses = validate_retake_status(st.session_state.courses)
    
    safe_execute(_update, "데이터 업데이트 중 오류가 발생했습니다")

def _add_row() -> None:
    df = st.session_state.courses.copy()
    if df.empty:
        base_year = pd.Timestamp.now().year
    else:
        base_year = int(df["연도"].iloc[-1])
    
    # 안전한 이수구분 설정
    categories = get_current_categories()
    default_category = categories[0] if categories else "공통교양"
    
    new_row = {
        "과목명": "",
        "학점": 3.0,
        "성적": "A0",
        "이수구분": default_category,
        "연도": base_year,
        "학기": TERM_OPTIONS[0],
        "재수강": False,
    }
    new_df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    
    # ✅ 재수강 상태 검증 적용
    st.session_state.courses = validate_retake_status(new_df)

def _del_row() -> None:
    if not st.session_state.courses.empty:
        st.session_state.courses = st.session_state.courses.iloc[:-1].reset_index(drop=True)

###############################################################################
# 2-4. 🎨 NEW UI 개선 함수들 (v2.4 핵심 기능)
###############################################################################

def process_retake_courses_correct(courses_df):
    """재수강 과목 처리 - 최고 성적 반영 (고려대학교 규정 준수)"""
    if courses_df.empty:
        return courses_df, []
    
    processed_courses = []
    duplicate_warnings = []
    
    # 과목명별 그룹화[7][20]
    course_groups = courses_df.groupby('과목명')
    
    for course_name, group in course_groups:
        if len(group) > 1:  # 동일 과목명이 여러 개인 경우
            # 재수강 표시가 없는 중복 과목 감지
            non_retake_courses = group[group['재수강'] == False]
            if len(non_retake_courses) > 1:
                duplicate_warnings.append(
                    f"'{course_name}' 과목이 {len(non_retake_courses)}번 수강되었으나 재수강 표시가 없습니다."
                )
            
            # 최고 성적 선택 (재수강 규정 적용)[8][11]
            valid_grades = group[group['성적'].isin(GRADE_MAP_45.keys())]
            if not valid_grades.empty:
                # 성적을 평점으로 변환하여 최고 성적 과목 선택
                valid_grades = valid_grades.copy()
                valid_grades['grade_point'] = valid_grades['성적'].map(GRADE_MAP_45)
                valid_grades = valid_grades.dropna(subset=['grade_point'])
                
                if not valid_grades.empty:
                    # 최고 평점 과목 선택 (동점일 경우 최신 과목)[7]
                    max_grade_point = valid_grades['grade_point'].max()
                    best_courses = valid_grades[valid_grades['grade_point'] == max_grade_point]
                    
                    if len(best_courses) > 1:
                        # 동점일 경우 최신 과목 선택 (연도, 학기 순)
                        best_course = best_courses.sort_values(['연도', '학기'], ascending=[False, False]).iloc[0]
                    else:
                        best_course = best_courses.iloc[0]
                    
                    processed_courses.append(best_course)
                else:
                    processed_courses.append(group.iloc[0])  # 평점이 없는 경우 첫 번째 선택
            else:
                processed_courses.append(group.iloc[0])
        else:
            processed_courses.append(group.iloc[0])
    
    return pd.DataFrame(processed_courses), duplicate_warnings

def calculate_gpa_with_correct_retake_handling(courses_df):
    """재수강 처리를 포함한 정확한 GPA 계산"""
    if courses_df.empty:
        return 0.0, 0.0, 0.0, []
    
    # 재수강 과목 처리
    processed_courses, warnings = process_retake_courses_correct(courses_df)
    
    # GPA 계산 대상 과목 필터링 (P/NP 제외)[19][21]
    gpa_courses = processed_courses[
        processed_courses['성적'].map(GRADE_MAP_45).notna()
    ].copy()
    
    if gpa_courses.empty:
        return 0.0, 0.0, 0.0, warnings
    
    # 평점 계산[14][19]
    gpa_courses['grade_points'] = gpa_courses['성적'].map(GRADE_MAP_45) * gpa_courses['학점']
    
    total_credits = gpa_courses['학점'].sum()
    total_grade_points = gpa_courses['grade_points'].sum()
    
    current_gpa = total_grade_points / total_credits if total_credits > 0 else 0.0
    
    return current_gpa, total_credits, total_grade_points, warnings

def render_dashboard():
    """🎨 대시보드 스타일 메인 화면 (실시간 현황 표시) - 오류 수정"""
    if not st.session_state.courses.empty:
        # 실시간 계산 (간단 버전)
        df = st.session_state.courses.copy()
        
        try:
            if "과목명" in df.columns and not df.empty:
                df["과목명"] = df["과목명"].fillna("").astype(str)
                valid_courses = df[df["과목명"].str.strip() != ""]
            else:
                valid_courses = pd.DataFrame()
        except pd.errors.EmptyDataError:
            st.error("⚠️ 데이터가 비어있습니다.")
            valid_courses = pd.DataFrame()
        except KeyError as e:
            st.error(f"⚠️ 필수 컬럼이 없습니다: {e}")
            valid_courses = pd.DataFrame()
        except pd.errors.DtypeWarning:
            st.warning("⚠️ 데이터 타입 경고가 있지만 계속 진행합니다.")
            # 기본 처리 계속
            valid_courses = df[df["과목명"].fillna("").astype(str).str.strip() != ""]
        except ValueError as e:
            st.error(f"⚠️ 데이터 형식 오류: {e}")
            st.info("💡 **해결 방법**: 과목명이 올바른 형식인지 확인하세요.")
            valid_courses = pd.DataFrame()
        except Exception as e:
            st.error(f"⚠️ 예상치 못한 오류가 발생했습니다: {str(e)}")
            st.info("💡 **해결 방법**: 페이지를 새로고침하거나 백업 데이터를 복원해보세요.")
            valid_courses = pd.DataFrame()

        
        if not valid_courses.empty:
            st.markdown("### 📊 실시간 학습 현황")
            
            # 간단한 실시간 통계 계산
            total_courses = len(valid_courses)
            total_credits = valid_courses["학점"].sum()
            
            # ✅ 수정된 GPA 계산 (최고 성적 반영)
            current_gpa, gpa_credits, _, warnings = calculate_gpa_with_correct_retake_handling(valid_courses)

            # 중복 과목 경고 표시
            if warnings:
                st.warning("⚠️ **중복 과목 감지**")
                for warning in warnings:
                    st.error(f"• {warning}")
                st.info("💡 **해결 방법**: 이전에 수강한 과목에 '재수강' 체크박스를 표시하세요.")
            
            # 대시보드 메트릭 표시
            dash_cols = st.columns(4)
            with dash_cols[0]:
                st.metric("📚 수강 과목", f"{total_courses}개", help="현재 입력된 전체 과목 수")
            with dash_cols[1]:
                st.metric("⭐ 누적 학점", f"{total_credits:.0f}학점", help="현재까지 취득한 총 학점")
            with dash_cols[2]:
                st.metric("🎯 현재 GPA", f"{current_gpa:.2f}", 
                         delta=f"{current_gpa - 3.0:.2f}" if current_gpa >= 3.0 else None, 
                         help="재수강 처리된 정확한 GPA")
            with dash_cols[3]:
                total_required = st.session_state.custom_requirements["총계"]["required"]
                remaining = max(0, total_required - total_credits)
                if remaining == 0:
                    st.metric("🎉 졸업 달성", "완료!", delta="축하합니다!", delta_color="normal")
                else:
                    st.metric("⏳ 남은 학점", f"{remaining:.0f}학점", delta="졸업까지", 
                             delta_color="inverse", help="졸업 요건까지 남은 학점")
            st.markdown("---")
        else:
            # 유효한 과목이 없을 때 기본 대시보드
            st.markdown("### 📊 학습 현황")
            st.info("📝 과목을 입력하면 실시간 현황을 확인할 수 있습니다.")
            
            # 기본 메트릭 (모두 0)
            dash_cols = st.columns(4)
            with dash_cols[0]:
                st.metric("📚 수강 과목", "0개")
            with dash_cols[1]:
                st.metric("⭐ 누적 학점", "0학점")
            with dash_cols[2]:
                st.metric("🎯 현재 GPA", "0.00")
            with dash_cols[3]:
                total_required = st.session_state.custom_requirements["총계"]["required"]
                st.metric("⏳ 남은 학점", f"{total_required}학점")
            
            st.markdown("---")

def analyze_current_progress():
    """현재 학습 상태 분석 - 오류 수정"""
    if st.session_state.courses.empty:
        return {
            '부족한_영역': [],
            '학기당_평균학점': 0,
            '최근_gpa_하락': False,
            '추천사항': []
        }
    
    df = st.session_state.courses.copy()
    
    # 🔥 안전한 필터링
    try:
        if "과목명" in df.columns and not df.empty:
            df["과목명"] = df["과목명"].fillna("").astype(str)
            valid_courses = df[df["과목명"].str.strip() != ""]
        else:
            valid_courses = pd.DataFrame()
    except Exception:
        valid_courses = pd.DataFrame()
    
    if valid_courses.empty:
        return {
            '부족한_영역': [],
            '학기당_평균학점': 0,
            '최근_gpa_하락': False,
            '추천사항': []
        }
    
    # 재수강 중복 제거
    deduped = valid_courses.sort_values("재수강").drop_duplicates(subset=["과목명"], keep="last")
    
    # 1. 부족한 영역 분석
    current_requirements = get_current_requirements()
    category_summary = {}
    
    for cat in get_current_categories():
        cat_credits = deduped[deduped["이수구분"] == cat]["학점"].sum()
        required = current_requirements.get(cat, {"required": 0})["required"]
        category_summary[cat] = {
            'earned': cat_credits,
            'required': required,
            'progress': cat_credits / required if required > 0 else 1.0
        }
    
    부족한_영역 = [cat for cat, info in category_summary.items() 
                  if info['progress'] < 0.5 and info['required'] > 0]
    
    # 2. 학기당 평균 학점 계산
    if not deduped.empty:
        semester_groups = deduped.groupby(['연도', '학기'])['학점'].sum()
        학기당_평균학점 = semester_groups.mean() if len(semester_groups) > 0 else 0
    else:
        학기당_평균학점 = 0
    
    # 3. 최근 GPA 하락 체크
    최근_gpa_하락 = False
    if len(deduped) > 5:  # 충분한 데이터가 있을 때만
        try:
            # 최근 2학기 vs 이전 2학기 비교 (간단한 휴리스틱)
            recent_courses = deduped.tail(5)
            earlier_courses = deduped.head(5) if len(deduped) > 10 else deduped.head(len(deduped)//2)
            
            recent_gpa_courses = recent_courses[recent_courses["성적"].map(GRADE_MAP_45).notna()]
            earlier_gpa_courses = earlier_courses[earlier_courses["성적"].map(GRADE_MAP_45).notna()]
            
            if not recent_gpa_courses.empty and not earlier_gpa_courses.empty:
                recent_gpa = (recent_gpa_courses["학점"] * recent_gpa_courses["성적"].map(GRADE_MAP_45)).sum() / recent_gpa_courses["학점"].sum()
                earlier_gpa = (earlier_gpa_courses["학점"] * earlier_gpa_courses["성적"].map(GRADE_MAP_45)).sum() / earlier_gpa_courses["학점"].sum()
                
                if recent_gpa < earlier_gpa - 0.3:  # 0.3 이상 하락 시
                    최근_gpa_하락 = True
        except:
            pass
    
    return {
        '부족한_영역': 부족한_영역,
        '학기당_평균학점': 학기당_평균학점,
        '최근_gpa_하락': 최근_gpa_하락,
        '추천사항': []
    }

def add_quick_course(category, credits, grade):
    """빠른 과목 추가"""
    df = st.session_state.courses.copy()
    
    # 기본값 설정
    try:
        current_year = pd.Timestamp.now().year
    except:
        current_year = 2025  # 안전한 기본값
    
    course_number = len(df[df["과목명"].str.strip() != ""]) + 1
    
    new_row = {
        "과목명": f"{category} 과목{course_number}",
        "학점": credits,
        "성적": grade,
        "이수구분": category,
        "연도": current_year,
        "학기": "1학기",
        "재수강": False,
    }
    
    st.session_state.courses = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    st.success(f"✅ {category} {credits}학점 과목이 추가되었습니다!")
    st.rerun()

def render_smart_input_helper():
    """🎨 스마트 입력 도우미 (맞춤형 추천)"""
    with st.expander("💡 스마트 입력 도우미", expanded=False):
        st.markdown("**🧠 맞춤형 입력 도움 & 추천사항**")
        
        # 현재 상태 분석
        current_progress = analyze_current_progress()
        
        # 동적 추천 생성
        recommendations = []
        
        if current_progress['부족한_영역']:
            top_lacking = current_progress['부족한_영역'][:2]  # 상위 2개만
            recommendations.append(f"📚 **우선 이수 추천**: {', '.join(top_lacking)}")
        
        if current_progress['학기당_평균학점'] > 20:
            recommendations.append("⚠️ **학습 부담 주의**: 학기당 평균 학점이 높습니다. 적정 수준으로 조절을 고려하세요.")
        elif current_progress['학기당_평균학점'] < 12 and current_progress['학기당_평균학점'] > 0:
            recommendations.append("🚀 **학습 속도 향상**: 더 많은 과목 수강을 고려해보세요.")
        
        if current_progress['최근_gpa_하락']:
            recommendations.append("📈 **성적 개선 필요**: 최근 GPA가 하락했습니다. 학습 전략을 점검해보세요.")
        
        # 일반적인 도움말
        if not recommendations:
            recommendations.append("📖 **균형잡힌 수강**: 교양과 전공을 적절히 배분하여 수강하세요.")
            recommendations.append("🎯 **목표 설정**: 목표 GPA를 설정하고 계획적으로 학습하세요.")
        
        # 추천사항 표시 (최대 3개)
        for rec in recommendations[:3]:
            st.info(rec)
        
        st.markdown("---")
        
        # 빠른 입력 버튼들
        st.markdown("**⚡ 빠른 과목 추가:**")
        quick_cols = st.columns(4)
        
        # 현재 부족한 영역 우선 표시
        available_categories = get_current_categories()
        priority_categories = []
        
        if current_progress['부족한_영역']:
            priority_categories = [cat for cat in current_progress['부족한_영역'] if cat in available_categories]
        
        # 기본 카테고리들 추가
        if not priority_categories:
            priority_categories = ["공통교양", "전공필수", "전공선택", "핵심교양"]
        
        # 최대 4개까지만 표시
        for i, category in enumerate(priority_categories[:4]):
            with quick_cols[i]:
                if st.button(f"📚 {category[:4]}", key=f"quick_{category}", use_container_width=True):
                    add_quick_course(category, 3.0, "A0")
        
        st.markdown("---")
        
        # 입력 팁
        with st.expander("💡 입력 효율성 팁"):
            st.markdown("""
            **⚡ 빠른 입력 방법:**
            - **복사-붙여넣기**: 엑셀에서 여러 과목을 한번에 복사 가능
            - **키보드 단축키**: Tab 키로 다음 칸으로 이동
            - **자동완성**: 과목명 입력 시 이전 입력 기록 참고
            
            **📝 정확한 입력을 위한 체크리스트:**
            - ✅ 과목명이 정확한지 확인
            - ✅ 학점이 올바른지 확인 (1, 2, 3학점만 가능)
            - ✅ 이수구분이 정확한지 확인
            - ✅ 재수강 과목은 반드시 체크
            """)

def render_enhanced_user_guide():
    """🎨 간소화된 사용자 가이드 (3개 탭)"""
    with st.expander("📚 사용 가이드", expanded=False):
        guide_tabs = st.tabs(["🚀 시작하기", "📝 데이터 입력", "❓ 문제해결"])
        
        with guide_tabs[0]:
            st.markdown("### 🚀 시작하기")
            st.markdown("""
            **고려대학교 GPA 계산기에 오신 것을 환영합니다!**
            
            **📋 준비사항:**
            1. 수강한 모든 과목의 성적표
            2. 본인의 전공 유형 확인 (심화/복수/이중전공)
            3. 학과별 졸업 요건 확인
            
            **🎯 사용 순서:**
            1. 👈 사이드바에서 전공 유형 설정
            2. 📝 과목 정보 입력 또는 CSV 가져오기
            3. 📊 계산하기 버튼 클릭
            4. 📈 결과 확인 및 목표 GPA 설정
            """)
        
        with guide_tabs[1]:
            st.markdown("### 📝 데이터 입력")
            
            input_method = st.radio(
                "입력 방법을 선택하세요:",
                ["🔢 직접 입력", "📁 CSV 파일"],
                key="guide_input_method"
            )
            
            if input_method == "🔢 직접 입력":
                st.markdown("""
                **직접 입력 방법:**
                - ➕ 행 추가 버튼으로 새 과목 추가
                - 과목명, 학점, 성적, 이수구분, 연도, 학기 입력
                - 재수강 과목은 반드시 체크
                """)
            else:
                st.markdown("""
                **CSV 파일 가져오기:**
                - 사이드바 → 📁 데이터 관리 → 📥 가져오기
                - 필수 컬럼: 과목명, 학점, 성적, 이수구분, 연도, 학기, 재수강
                - 본 프로그램에서 내보낸 파일 권장
                """)
        
        with guide_tabs[2]:
            st.markdown("### ❓ 문제해결")
            
            problem = st.selectbox(
                "어떤 문제가 있나요?",
                ["계산 결과 이상", "데이터 사라짐", "전공 유형 변경 불가", "CSV 가져오기 실패"],
                key="guide_problem"
            )
            
            if problem == "계산 결과 이상":
                st.markdown("""
                **해결 방법:**
                - 재수강 과목 체크 확인
                - 이수구분 정확성 확인
                - 과목명, 학점, 성적 오타 확인
                """)
            elif problem == "데이터 사라짐":
                st.markdown("""
                **복구 방법:**
                - 사이드바 → ↩️ 복원 버튼 (백업이 있는 경우)
                - 이전 CSV 파일로 복구
                - 정기적 백업 권장
                """)
            elif problem == "전공 유형 변경 불가":
                st.markdown("""
                **해결 방법:**
                - 호환되지 않는 과목의 이수구분 수정
                - 예: 심화→복수 시 '복수전공 필수' 과목 제거 필요
                """)
            else:
                st.markdown("""
                **해결 방법:**
                - UTF-8 인코딩 CSV 파일 사용
                - 필수 컬럼 포함 확인
                - 본 프로그램에서 내보낸 파일 사용
                """)

###############################################################################
# 2-4. 데이터 검증 및 자동 수정 시스템
###############################################################################

class DataIntegrityManagerFixed:
    """데이터 무결성 관리 클래스 - 개선된 버전"""
    
    def __init__(self):
        self.issues_found = []
        self.fixes_applied = []
        self.warnings = []
        self.critical_errors = []

    def validate_and_fix_all(self) -> bool:
        """모든 데이터 검증 및 자동 수정 - 개선된 예외 처리"""
        try:
            self.issues_found.clear()
            self.fixes_applied.clear()
            self.warnings.clear()
            self.critical_errors.clear()

            # 1. 이수구분-전공유형 일치성 검증 (간소화된 스킵 조건)
            self._validate_category_major_type_consistency_fixed()
            
            # 2. 학점 합계 논리적 일치성 검증
            self._validate_credit_logical_consistency_enhanced()
            
            # 3. 재수강 과목 중복 검증 (강화)
            self._validate_retake_duplicates_enhanced()
            
            # 4. 졸업요건 논리적 일치성 검증
            self._validate_graduation_requirements_consistency_fixed()
            
            # 5. 데이터 타입 일관성 검증 (새로 추가)
            self._validate_data_type_consistency()

            # 수정 사항이 있으면 로그 표시
            if self.issues_found or self.critical_errors:
                self._display_fix_log()
                return True
            return False
            
        except pd.errors.EmptyDataError:
            st.error("⚠️ 데이터 검증 실패: 데이터가 비어있습니다.")
            self.critical_errors.append("EmptyDataError: 데이터가 비어있음")
            return False
        except KeyError as e:
            st.error(f"⚠️ 데이터 검증 실패: 필수 데이터가 없습니다 - {e}")
            self.critical_errors.append(f"KeyError: {e}")
            return False
        except pd.errors.ParserError as e:
            st.error(f"⚠️ 데이터 파싱 오류: {e}")
            self.critical_errors.append(f"ParserError: {e}")
            return False
        except Exception as e:
            st.error(f"⚠️ 데이터 검증 중 예상치 못한 오류: {e}")
            st.info("💡 **해결 방법**: 데이터를 다시 입력하거나 백업에서 복원하세요.")
            self.critical_errors.append(f"UnexpectedError: {e}")
            return False

    def _validate_category_major_type_consistency_fixed(self):
        """이수구분-전공유형 일치성 검증 - 간소화된 스킵 조건"""
        try:
            if st.session_state.courses.empty:
                return
            
            # ✅ CSV 가져오기 직후에만 스킵 (간소화)
            if st.session_state.get("csv_import_just_completed", False):
                return
            
            df = st.session_state.courses
            major_type = st.session_state.major_type

            # 문제 상황 감지 및 수정
            if major_type == "심화전공":
                invalid_cats = ["이중전공 필수", "이중전공 선택", "복수전공 필수", "복수전공 선택"]
                for cat in invalid_cats:
                    invalid_courses = df[df["이수구분"] == cat]
                    if not invalid_courses.empty:
                        self.issues_found.append(f"심화전공 설정인데 '{cat}' 과목이 {len(invalid_courses)}개 있습니다")
                        
                        # 자동 수정: 적절한 카테고리로 변환
                        if "필수" in cat:
                            df.loc[df["이수구분"] == cat, "이수구분"] = "전공필수"
                            self.fixes_applied.append(f"'{cat}' → '전공필수'로 자동 변환 ({len(invalid_courses)}과목)")
                        else:
                            df.loc[df["이수구분"] == cat, "이수구분"] = "전공선택"
                            self.fixes_applied.append(f"'{cat}' → '전공선택'으로 자동 변환 ({len(invalid_courses)}과목)")

            elif major_type in ["이중전공", "복수전공"]:
                # 이중/복수전공인데 심화전공 관련 설정이 있는 경우
                if "심화전공" in st.session_state.custom_requirements:
                    self.issues_found.append(f"{major_type} 설정인데 심화전공 요건이 설정되어 있습니다")
                    del st.session_state.custom_requirements["심화전공"]
                    self.fixes_applied.append("심화전공 요건을 자동으로 제거했습니다")
                    
        except Exception as e:
            self.warnings.append(f"이수구분-전공유형 일치성 검증 중 오류: {e}")

    def _validate_credit_logical_consistency_enhanced(self):
        """학점 합계 논리적 일치성 검증 - 강화된 버전"""
        try:
            if st.session_state.courses.empty:
                return

            df = st.session_state.courses
            
            # 1. 음수 학점 검증
            try:
                negative_credits = df[df["학점"] < 0]
                if not negative_credits.empty:
                    self.issues_found.append(f"음수 학점이 {len(negative_credits)}개 발견되었습니다")
                    df.loc[df["학점"] < 0, "학점"] = 3.0
                    self.fixes_applied.append(f"음수 학점을 3.0으로 자동 수정했습니다")
            except (TypeError, KeyError):
                self.warnings.append("학점 데이터 타입 검증 실패")

            # 2. 비정상적으로 높은 학점 검증 (10학점 초과)
            try:
                high_credits = df[df["학점"] > 10]
                if not high_credits.empty:
                    max_credit = high_credits["학점"].max()
                    self.issues_found.append(f"비정상적으로 높은 학점({max_credit:.1f})이 발견되었습니다")
                    df.loc[df["학점"] > 10, "학점"] = 3.0
                    self.fixes_applied.append(f"비정상 학점을 3.0으로 자동 수정했습니다")
            except (TypeError, KeyError):
                self.warnings.append("고학점 검증 실패")

            # 3. 0학점 과목 검증 (특수한 경우)
            try:
                zero_credits = df[df["학점"] == 0]
                if not zero_credits.empty:
                    self.warnings.append(f"0학점 과목이 {len(zero_credits)}개 있습니다")
                    # 0학점은 경고만 표시 (자동 수정하지 않음)
            except (TypeError, KeyError):
                pass

        except Exception as e:
            self.warnings.append(f"학점 논리적 일치성 검증 중 오류: {e}")

    def _validate_retake_duplicates_enhanced(self):
        """재수강 과목 중복 검증 - 강화된 버전"""
        try:
            if st.session_state.courses.empty:
                return

            df = st.session_state.courses

            # ✅ 빈 과목명 필터링 추가
            # 과목명이 비어있거나 공백만 있는 행은 검증에서 제외
            valid_courses = df[
                (df['과목명'].notna()) & 
                (df['과목명'].astype(str).str.strip() != "")
            ]

            if valid_courses.empty:
                return
            
            # 1. 동일 과목명에서 재수강 표시가 없는 중복 확인
            course_groups = valid_courses.groupby('과목명')
            
            for course_name, group in course_groups:
                if len(group) > 1:
                    non_retake_courses = group[group['재수강'] == False]
                    
                    # 재수강 표시가 없는 중복이 2개 이상인 경우
                    if len(non_retake_courses) > 1:
                        self.issues_found.append(f"'{course_name}' 과목에 재수강 표시가 없는 중복이 {len(non_retake_courses)}개 있습니다")
                        
                        # 자동 수정: 최신 과목을 제외하고 재수강 표시
                        if '연도' in group.columns and '학기' in group.columns:
                            try:
                                # 연도, 학기 순으로 정렬해서 최신 것 제외하고 재수강 표시
                                sorted_courses = non_retake_courses.sort_values(['연도', '학기'], ascending=[True, True])
                                retake_indices = sorted_courses.index[:-1]  # 마지막(최신) 제외
                                
                                df.loc[retake_indices, '재수강'] = True
                                self.fixes_applied.append(f"'{course_name}' 과목의 이전 수강분에 재수강 표시를 자동 추가했습니다")
                            except Exception as e:
                                self.warnings.append(f"'{course_name}' 재수강 표시 자동 추가 실패: {e}")

        except Exception as e:
            self.warnings.append(f"재수강 중복 검증 중 오류: {e}")

    def _validate_graduation_requirements_consistency_fixed(self):
        """졸업요건 논리적 일치성 검증 - 전공 유형 변경 시 스킵"""
        try:
            # ✅ 전공 유형 변경 중에는 검증 스킵 (이 부분만 추가)
            if st.session_state.get("major_type_changing", False):
                st.session_state.major_type_changing = False  # 플래그 초기화
                return

            requirements = st.session_state.custom_requirements

            # 1. 총 졸업학점 자동 계산 (기존 코드 유지)
            auto_total = 0
            for category, req in requirements.items():
                if category != "총계":
                    auto_total += req["required"]

            current_total = requirements.get("총계", {"required": 130})["required"]
            if current_total != auto_total:
                self.issues_found.append(f"총 졸업학점({current_total})이 영역별 합계({auto_total})와 일치하지 않습니다")
                st.session_state.custom_requirements["총계"]["required"] = auto_total
                self.fixes_applied.append(f"총 졸업학점을 {auto_total}학점으로 자동 수정했습니다")
            
            # 2. 음수 학점 검증
            for cat, req in requirements.items():
                if req["required"] < 0:
                    self.issues_found.append(f"'{cat}' 요구 학점이 음수입니다")
                    st.session_state.custom_requirements[cat]["required"] = 0
                    self.fixes_applied.append(f"'{cat}' 요구 학점을 0으로 수정했습니다")
            
            # 3. 비현실적으로 높은 학점 검증 (각 영역당 100학점 초과)
            for cat, req in requirements.items():
                if cat != "총계" and req["required"] > 100:
                    self.issues_found.append(f"'{cat}' 요구 학점({req['required']})이 비현실적으로 높습니다")
                    st.session_state.custom_requirements[cat]["required"] = 30
                    self.fixes_applied.append(f"'{cat}' 요구 학점을 30학점으로 조정했습니다")
                    
        except Exception as e:
            self.warnings.append(f"졸업 요건 일치성 검증 중 오류: {e}")

    def _validate_data_type_consistency(self):
        """데이터 타입 일관성 검증 - 새로 추가된 기능"""
        try:
            if st.session_state.courses.empty:
                return

            df = st.session_state.courses
            
            # 필수 컬럼 존재 여부 확인
            required_columns = ['과목명', '학점', '성적', '이수구분', '연도', '학기', '재수강']
            missing_columns = [col for col in required_columns if col not in df.columns]
            
            if missing_columns:
                self.issues_found.append(f"필수 컬럼이 누락되었습니다: {', '.join(missing_columns)}")
                # 누락된 컬럼 자동 추가
                for col in missing_columns:
                    if col == '과목명':
                        df[col] = ""
                    elif col == '학점':
                        df[col] = 3.0
                    elif col == '성적':
                        df[col] = "A0"
                    elif col == '이수구분':
                        df[col] = "공통교양"
                    elif col == '연도':
                        df[col] = pd.Timestamp.now().year
                    elif col == '학기':
                        df[col] = "1학기"
                    elif col == '재수강':
                        df[col] = False
                self.fixes_applied.append(f"누락된 컬럼을 기본값으로 추가했습니다: {', '.join(missing_columns)}")
            
            # 데이터 타입 검증 및 수정
            try:
                df['학점'] = pd.to_numeric(df['학점'], errors='coerce').fillna(3.0)
                df['연도'] = pd.to_numeric(df['연도'], errors='coerce').fillna(pd.Timestamp.now().year).astype(int)
                df['과목명'] = df['과목명'].astype(str)
                df['성적'] = df['성적'].astype(str)
                df['이수구분'] = df['이수구분'].astype(str)
                df['학기'] = df['학기'].astype(str)
                df['재수강'] = df['재수강'].astype(bool)
                
                st.session_state.courses = df
                
            except Exception as e:
                self.warnings.append(f"데이터 타입 변환 중 오류: {e}")
                
        except Exception as e:
            self.warnings.append(f"데이터 타입 일관성 검증 중 오류: {e}")

    def _display_fix_log(self):
        """수정 로그 표시 - 개선된 버전"""
        if self.critical_errors:
            st.error("🚨 **심각한 오류가 발생했습니다**")
            for error in self.critical_errors:
                st.error(f"• {error}")
            st.info("💡 **권장사항**: 페이지를 새로고침하거나 백업 데이터를 복원하세요.")
        
        if self.issues_found:
            st.warning("⚠️ **데이터 무결성 문제가 발견되어 자동으로 수정되었습니다**")
            
            with st.expander("📋 수정 내역 상세보기", expanded=True):
                for i, (issue, fix) in enumerate(zip(self.issues_found, self.fixes_applied)):
                    st.markdown(f"""
                    **문제 {i+1}**: {issue}  
                    **✅ 해결**: {fix}
                    """)
                
                if self.warnings:
                    st.markdown("**⚠️ 경고사항:**")
                    for warning in self.warnings:
                        st.warning(f"• {warning}")
                
                st.info("💡 **권장사항**: 데이터가 자동으로 수정되었지만, 수동으로 한번 더 확인해보세요.")
                
                # 재검증 버튼
                if st.button("🔄 데이터 재검증", key="revalidate_data"):
                    st.rerun()


###############################################################################
# 2-5. 🎨 진행률 표시 개선 함수 (누락된 함수 추가)
###############################################################################

def render_enhanced_progress_with_guidance(summary_df, current_requirements, misc):
    """🎨 간소화된 진행률 표시"""
    st.subheader("🎯 졸업 요건 진행률")
    
    # 전체 진행률 분석
    total_required = current_requirements["총계"]["required"]
    overall_progress = misc["earned_credits"] / total_required
    
    # 단계별 안내 메시지 (전체적인 안내만)
    if overall_progress < 0.3:
        st.info("🌱 **초기 단계**: 기본 교양과목을 중심으로 수강하세요.")
    elif overall_progress < 0.7:
        st.info("🌿 **중반 단계**: 전공과목 비중을 늘려가세요.")
    else:
        st.success("🌳 **후반 단계**: 졸업 요건을 점검하고 부족한 영역을 채우세요.")
    
    
    progress_cols = st.columns([1, 1])
    
    # 진행률 바 (개별 안내 메시지 제거)
    for i, (_, r) in enumerate(summary_df.iterrows()):
        col_idx = i % 2
        with progress_cols[col_idx]:
            need = current_requirements.get(r["영역"], {"required": 0})["required"]
            if need > 0:
                adjusted_earned = r["이수학점"]
                actual_pct = adjusted_earned / need
                
                display_pct = min(actual_pct, 1.0)
                display_percentage = min(actual_pct * 100, 100.0)
                color = get_progress_color(actual_pct)
                
                # 진행률 바 HTML (안내 메시지 제거)
                st.markdown(f"""
                <div class="progress-container">
                    <div class="progress-label">
                        {r['영역']}: {adjusted_earned:.0f}/{need}학점 ({display_percentage:.1f}%)
                    </div>
                    <div class="progress-bar-bg">
                        <div class="progress-bar-fill" style="width: {display_pct*100:.1f}%; background-color: {color};"></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
    
    # 총계 진행률 (안내 메시지 제거)
    actual_overall_pct = misc["earned_credits"] / total_required
    display_overall_pct = min(actual_overall_pct, 1.0)
    display_overall_percentage = min(actual_overall_pct * 100, 100.0)
    overall_color = get_progress_color(actual_overall_pct)
    
    st.markdown(f"""
    <div class="progress-container">
        <div class="progress-label" style="font-size: 1rem; font-weight: bold;">
            **총계: {misc['earned_credits']:.0f}/{total_required}학점 ({display_overall_percentage:.1f}%)**
        </div>
        <div class="progress-bar-bg" style="height: 1.2rem;">
            <div class="progress-bar-fill" style="width: {display_overall_pct*100:.1f}%; background-color: {overall_color};"></div>
        </div>
    </div>
    """, unsafe_allow_html=True)

###############################################################################
# 3. 전공 유형 변경 검증 시스템
###############################################################################

def check_major_type_compatibility(new_major_type: str) -> Tuple[bool, List[str]]:
    """전공 유형 변경 호환성 검사"""
    if st.session_state.courses.empty:
        return True, []
    
    df = st.session_state.courses
    current_categories = df["이수구분"].unique()
    incompatible_courses = []
    
    if new_major_type == "심화전공":
        # 심화전공으로 변경 시 이중/복수전공 과목 확인
        invalid_cats = ["이중전공 필수", "이중전공 선택", "복수전공 필수", "복수전공 선택"]
        for cat in invalid_cats:
            if cat in current_categories:
                count = len(df[df["이수구분"] == cat])
                incompatible_courses.append(f"'{cat}' 과목 {count}개")
    
    elif new_major_type == "이중전공":
        # 이중전공으로 변경 시 복수전공 과목 확인
        invalid_cats = ["복수전공 필수", "복수전공 선택"]
        for cat in invalid_cats:
            if cat in current_categories:
                count = len(df[df["이수구분"] == cat])
                incompatible_courses.append(f"'{cat}' 과목 {count}개")
    
    elif new_major_type == "복수전공":
        # 복수전공으로 변경 시 이중전공 과목 확인
        invalid_cats = ["이중전공 필수", "이중전공 선택"]
        for cat in invalid_cats:
            if cat in current_categories:
                count = len(df[df["이수구분"] == cat])
                incompatible_courses.append(f"'{cat}' 과목 {count}개")
    
    return len(incompatible_courses) == 0, incompatible_courses

def update_major_type_with_validation():
    """검증을 포함한 전공 유형 업데이트 - Session State 경고 해결"""
    if "major_type_widget" in st.session_state:
        new_major_type = st.session_state.major_type_widget
        current_major_type = st.session_state.major_type
        
        if new_major_type != current_major_type:
            # ✅ 전공 유형 변경 플래그 설정 (무결성 검증 스킵용)
            st.session_state.major_type_changing = True
            
            # 호환성 검사
            is_compatible, incompatible_courses = check_major_type_compatibility(new_major_type)
            
            if not is_compatible:
                # 변경 차단 - session_state 직접 업데이트 (경고 없이)
                st.session_state.major_type_widget = current_major_type  # 롤백
                st.session_state.major_type_changing = False  # 플래그 리셋
                
                # 안내 메시지
                st.error(f"⚠️ **전공 유형을 '{new_major_type}'으로 변경할 수 없습니다**")
                st.markdown("**문제가 되는 과목들:**")
                for course_info in incompatible_courses:
                    st.markdown(f"• {course_info}")
                
                st.info("""
                📝 **해결 방법**:
                1. 위의 과목들의 이수구분을 적절히 변경하세요
                2. 또는 해당 과목들을 삭제하세요
                3. 변경 후 다시 전공 유형을 변경해보세요
                """)
                return
        
        # 호환성이 확인되면 정상 업데이트
        st.session_state.major_type = new_major_type
        
        # 기본 카테고리 업데이트 (커스텀 유지)
        base_categories = get_categories_by_major_type(new_major_type)
        custom_additions = [cat for cat in st.session_state.custom_categories 
                          if cat not in BASE_CATEGORIES and 
                             cat not in ["복수전공 필수", "복수전공 선택", "이중전공 필수", "이중전공 선택"]]
        
        st.session_state.custom_categories = base_categories + custom_additions
        
        # 졸업 요건 업데이트 (커스텀 유지)
        new_requirements = get_requirements_by_major_type(new_major_type)
        for cat, req in st.session_state.custom_requirements.items():
            if cat not in new_requirements and cat in custom_additions + ["학문의기초"]:
                new_requirements[cat] = req
        
        st.session_state.custom_requirements = new_requirements
        
        # 총 졸업학점 자동 계산
        auto_total = auto_calculate_total_credits(st.session_state.custom_requirements)
        st.session_state.custom_requirements["총계"]["required"] = auto_total
        
        invalidate_cache()
        
        # 성공 메시지
        if new_major_type != current_major_type:
            st.success(f"✅ 전공 유형이 '{new_major_type}'로 변경되었습니다!")


###############################################################################
# 4. 강화된 CSV 메타데이터 시스템
###############################################################################

def safe_json_parse(json_string, fallback=None):
    """안전한 JSON 파싱 with 구체적 에러 처리"""
    if not json_string or not json_string.strip():
        return fallback
    
    try:
        return json.loads(json_string)
    except json.JSONDecodeError as e:
        st.warning(f"JSON 형식 오류: {str(e)[:100]}")
        return fallback
    except (TypeError, ValueError) as e:
        st.warning(f"데이터 타입 오류: {str(e)[:100]}")
        return fallback
    except Exception as e:
        st.error(f"예상치 못한 오류: {str(e)[:100]}")
        return fallback

def validate_metadata(metadata):
    """메타데이터 유효성 검증"""
    if not isinstance(metadata, dict):
        return False, "메타데이터가 딕셔너리 형태가 아닙니다"
    
    required_keys = ["version", "major_type", "custom_categories", "custom_requirements"]
    missing_keys = [key for key in required_keys if key not in metadata]
    
    if missing_keys:
        return False, f"필수 키가 누락되었습니다: {', '.join(missing_keys)}"
    
    # 버전 호환성 검사
    if metadata.get("version") not in ["2.1", "2.2", "2.3", "2.4"]:
        return False, f"지원하지 않는 버전입니다: {metadata.get('version')}"
    
    # 전공 유형 검증
    if metadata.get("major_type") not in MAJOR_TYPE_OPTIONS:
        return False, f"올바르지 않은 전공 유형입니다: {metadata.get('major_type')}"
    
    return True, ""

def export_to_csv_with_metadata():
    """메타데이터 포함 CSV 내보내기"""
    try:
        if st.session_state.courses.empty:
            return None
        
        # 메타데이터 생성 (디버깅 메시지 제거)
        metadata = {
            "version": "2.4",
            "major_type": st.session_state.major_type,
            "custom_categories": st.session_state.custom_categories,
            "custom_requirements": st.session_state.custom_requirements,
            "target_gpa": st.session_state.target_gpa,
            "export_timestamp": pd.Timestamp.now().isoformat()
        }
        
        # 메타데이터를 JSON으로 인코딩
        metadata_json = json.dumps(metadata, ensure_ascii=False)
        metadata_b64 = base64.b64encode(metadata_json.encode('utf-8')).decode('ascii')
        
        # CSV 생성
        output = io.StringIO()
        output.write(f"# METADATA: {metadata_b64}\n")
        st.session_state.courses.to_csv(output, index=False, encoding='utf-8')
        
        return output.getvalue()
        
    except Exception as e:
        st.error(f"CSV 내보내기 실패: {str(e)}")
        return None

def import_from_csv_with_metadata(uploaded_file):
    """메타데이터 포함 CSV 가져오기"""
    def _import():
        try:
            # 파일 읽기
            content = uploaded_file.read().decode('utf-8')
            if not content.strip():
                raise ValueError("빈 파일입니다")
            
            lines = content.split('\n')
            metadata = None
            csv_start_line = 0
            
            # 메타데이터 파싱 (디버깅 메시지 제거)
            if lines and lines[0].startswith("# METADATA:"):
                try:
                    metadata_b64 = lines[0].replace("# METADATA: ", "")
                    metadata_json = base64.b64decode(metadata_b64).decode('utf-8')
                    metadata = safe_json_parse(metadata_json)
                    
                    if metadata:
                        is_valid, error_msg = validate_metadata(metadata)
                        if not is_valid:
                            st.warning(f"메타데이터 검증 실패: {error_msg}")
                            metadata = None
                        else:
                            csv_start_line = 1
                            st.info("✅ 메타데이터 검증 완료")
                    
                except Exception as e:
                    st.warning(f"메타데이터 디코딩 실패: {str(e)[:100]}")
                    metadata = None
        
            # CSV 데이터 파싱
            csv_content = '\n'.join(lines[csv_start_line:])
            if not csv_content.strip():
                raise ValueError("CSV 데이터가 없습니다")
            
            df = pd.read_csv(io.StringIO(csv_content))
            
            # 필수 컬럼 검증
            required_cols = ["과목명", "학점", "성적", "이수구분", "연도", "학기", "재수강"]
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                raise ValueError(f"필수 컬럼이 누락되었습니다: {', '.join(missing_cols)}")
            
            # 데이터 타입 검증 및 수정
            df["학점"] = pd.to_numeric(df["학점"], errors="coerce").fillna(3.0)
            df["연도"] = pd.to_numeric(df["연도"], errors="coerce").fillna(2025).astype(int)
            df["재수강"] = df["재수강"].astype(bool)
            
            # 🔥 핵심 수정: 전공 유형 변경 여부 추적
            major_type_changed = False
            original_major_type = st.session_state.major_type
            
            # 🔥 메타데이터가 있으면 설정 복원
            if metadata:
                try:
                    restored_major_type = metadata.get("major_type", "심화전공")
                    
                    if restored_major_type != original_major_type:
                        major_type_changed = True
                        
                        # 🔥 완전한 해결책: Widget 재생성을 위한 특수 처리
                        # 1. 기존 widget key 삭제
                        if "major_type_widget" in st.session_state:
                            del st.session_state.major_type_widget
                        
                        # 2. 전공 유형 업데이트
                        st.session_state.major_type = restored_major_type
                        
                        # 3. Widget 강제 재생성을 위한 플래그 설정
                        st.session_state.force_widget_recreation = True
                        st.session_state.new_major_type = restored_major_type
                        
                        # 4. 졸업 요건 완전히 교체
                        new_requirements = get_requirements_by_major_type(restored_major_type)
                        metadata_requirements = metadata.get("custom_requirements", {})
                        for cat, req in metadata_requirements.items():
                            new_requirements[cat] = req
                        st.session_state.custom_requirements = new_requirements
                        
                        # 5. 이수구분 업데이트
                        metadata_categories = metadata.get("custom_categories", [])
                        if metadata_categories:
                            st.session_state.custom_categories = metadata_categories
                        else:
                            base_categories = get_categories_by_major_type(restored_major_type)
                            imported_categories = df["이수구분"].unique()
                            additional_categories = [cat for cat in imported_categories 
                                                   if cat not in base_categories and cat and str(cat).strip()]
                            st.session_state.custom_categories = base_categories + additional_categories
                    else:
                        # 전공 유형이 같으면 모든 설정 복원
                        st.session_state.custom_categories = metadata.get("custom_categories", get_categories_by_major_type(restored_major_type))
                        st.session_state.custom_requirements = metadata.get("custom_requirements", DEFAULT_REQUIREMENTS.copy())
                    
                    # 목표 GPA 복원
                    st.session_state.target_gpa = metadata.get("target_gpa", 4.0)
                    
                    # 캐시 무효화
                    invalidate_cache()
                    
                    st.success(f"📋 설정 복원 완료: {restored_major_type} 전공")
                    if major_type_changed:
                        st.success(f"🔄 **전공 유형 변경 완료**: '{original_major_type}' → '{restored_major_type}'")
                    
                except Exception as e:
                    st.warning(f"설정 복원 실패: {str(e)}")
            else:
                # 메타데이터 없을 때의 추론 로직 (기존과 동일)
                imported_categories = df["이수구분"].unique()
                current_categories = get_current_categories()
                
                new_categories = []
                for cat in imported_categories:
                    if cat not in current_categories and cat and str(cat).strip():
                        st.session_state.custom_categories.append(str(cat).strip())
                        new_categories.append(str(cat).strip())
                
                detected_major_type = None
                dual_indicators = ["이중전공 필수", "이중전공 선택"]
                if any(cat in imported_categories for cat in dual_indicators):
                    detected_major_type = "이중전공"
                elif any(cat in ["복수전공 필수", "복수전공 선택"] for cat in imported_categories):
                    detected_major_type = "복수전공"
                
                if detected_major_type and detected_major_type != original_major_type:
                    major_type_changed = True
                    
                    # Widget 재생성 처리
                    if "major_type_widget" in st.session_state:
                        del st.session_state.major_type_widget
                    
                    st.session_state.major_type = detected_major_type
                    st.session_state.force_widget_recreation = True
                    st.session_state.new_major_type = detected_major_type
                    
                    # 졸업 요건 및 카테고리 업데이트
                    st.session_state.custom_requirements = get_requirements_by_major_type(detected_major_type)
                    base_categories = get_categories_by_major_type(detected_major_type)
                    st.session_state.custom_categories = base_categories + new_categories
                    
                    for cat in new_categories:
                        if cat not in st.session_state.custom_requirements:
                            st.session_state.custom_requirements[cat] = {"required": 0}
                    
                    invalidate_cache()
                    st.success(f"🔄 **전공 유형 자동 변경**: '{original_major_type}' → '{detected_major_type}'")
                else:
                    for cat in new_categories:
                        st.session_state.custom_requirements[cat] = {"required": 0}
                    if new_categories:
                        invalidate_cache()
                        st.info(f"📋 새로운 이수구분 추가: {', '.join(new_categories)}")
            
            # 데이터 저장
            st.session_state.courses = df
            
            # DataIntegrityManager 스킵 설정
            if major_type_changed:
                st.session_state.skip_integrity_check = True
                st.session_state.csv_import_just_completed = True
            
            st.rerun()
            return len(df)
            
        except pd.errors.EmptyDataError:
            raise ValueError("CSV 파일이 비어있습니다")
        except pd.errors.ParserError as e:
            raise ValueError(f"CSV 파싱 오류: {str(e)}")
        except UnicodeDecodeError:
            raise ValueError("파일 인코딩 오류. UTF-8 파일을 업로드하세요")
        except Exception as e:
            raise ValueError(f"파일 처리 오류: {str(e)}")
    
    result = safe_execute(_import, "파일 가져오기 실패")
    if result:
        st.success(f"✅ {result}개 과목이 성공적으로 가져와졌습니다!")

###############################################################################
# 5. 강화된 백업/복원 시스템
###############################################################################

def backup_data_enhanced():
    """강화된 데이터 백업 (모든 설정 포함)"""
    def _backup():
        backup_data = {
            "courses": st.session_state.courses.copy(),
            "custom_requirements": st.session_state.custom_requirements.copy(),
            "custom_categories": st.session_state.custom_categories.copy(),
            "major_type": st.session_state.major_type,
            "target_gpa": st.session_state.target_gpa,
            "backup_timestamp": pd.Timestamp.now().isoformat()
        }
        
        for key, value in backup_data.items():
            st.session_state[f"backup_{key}"] = value
    
    safe_execute(_backup, "백업 중 오류가 발생했습니다", "💾 모든 데이터와 설정이 백업되었습니다!")

def restore_data_enhanced():
    """강화된 데이터 복원 (모든 설정 포함)"""
    def _restore():
        if "backup_courses" not in st.session_state:
            raise ValueError("복원할 백업 데이터가 없습니다")
        
        # 모든 백업 데이터 복원
        restore_keys = ["courses", "custom_requirements", "custom_categories", "major_type", "target_gpa"]
        
        for key in restore_keys:
            backup_key = f"backup_{key}"
            if backup_key in st.session_state:
                st.session_state[key] = st.session_state[backup_key]
        
        # 백업 시간 표시
        if "backup_backup_timestamp" in st.session_state:
            backup_time = st.session_state["backup_backup_timestamp"]
            st.info(f"📅 백업 시점: {backup_time}")
        
        invalidate_cache()
        st.rerun()
        return True
    
    safe_execute(_restore, "복원 중 오류가 발생했습니다", "↩️ 모든 데이터와 설정이 복원되었습니다!")

###############################################################################
# 6. 초기화 실행 (안전한 위치로 이동)
###############################################################################

# 모든 핵심 함수 정의 완료 후 세션 초기화 실행
try:
    init_session()
except Exception as e:
    st.error(f"초기화 오류: {str(e)}")
    st.info("페이지를 새로고침해주세요.")

###############################################################################
# 7. 완전한 CSS 스타일링
###############################################################################

def apply_custom_css():
    """완전한 CSS 스타일링 - 애니메이션 제거 + 빨간색 밑줄 제거"""
    st.markdown("""
    <style>
    /* 🔥 CSS 변수 정의 */
    :root {
        --primary-color: #0066CC;
        --secondary-color: #FF6B35;
        --success-color: #28a745;
        --warning-color: #ffc107;
        --danger-color: #dc3545;
        --info-color: #17a2b8;
        --light-bg: #f8f9fa;
        --dark-bg: #343a40;
        --border-radius: 12px;
        --shadow-soft: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        --shadow-medium: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
        --transition: all 0.3s ease;
    }
    
    /* 🔥 개선된 탭 스타일 - 가독성 향상 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        margin-bottom: 1rem;
        flex-wrap: wrap;
    }
    
    .stTabs [data-baseweb="tab"] {
        border-radius: var(--border-radius);
        padding: 0.75rem 1.25rem;
        background: linear-gradient(145deg, var(--light-bg) 0%, #e9ecef 100%);
        border: 2px solid #dee2e6;
        color: #495057 !important;
        font-weight: 600 !important;
        font-size: 0.95rem !important;
        transition: var(--transition);
        min-width: 120px;
    }
    
    .stTabs [data-baseweb="tab"]:hover {
        background: linear-gradient(145deg, #e9ecef 0%, #dee2e6 100%);
        border-color: #6c757d;
        transform: translateY(-1px);
        box-shadow: var(--shadow-soft);
    }
    
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, var(--primary-color) 0%, #004499 100%) !important;
        color: white !important;
        border-color: var(--primary-color) !important;
        box-shadow: 0 4px 12px rgba(0, 102, 204, 0.3);
    }
    
    .stTabs [aria-selected="true"]:hover {
        background: linear-gradient(135deg, #0056b3 0%, #003d82 100%) !important;
    }
    
    /* 🔥 메트릭 카드 개선 */
    [data-testid="metric-container"] {
        background: linear-gradient(145deg, #ffffff 0%, var(--light-bg) 100%);
        border: 1px solid #e9ecef;
        border-radius: var(--border-radius);
        padding: 1.2rem;
        box-shadow: var(--shadow-soft);
        transition: var(--transition);
        margin-bottom: 1rem;
    }
    
    [data-testid="metric-container"]:hover {
        box-shadow: var(--shadow-medium);
        transform: translateY(-2px);
    }
    
    /* 🔥 버튼 스타일 개선 + 빨간색 밑줄 제거 */
    .stButton > button {
        border-radius: var(--border-radius);
        border: none;
        padding: 0.6rem 1.2rem;
        font-weight: 600;
        transition: var(--transition);
        position: relative;
        overflow: hidden;
        text-decoration: none !important; /* 밑줄 제거 */
    }
    
    .stButton > button:hover {
        transform: translateY(-1px);
        box-shadow: var(--shadow-soft);
        text-decoration: none !important; /* 호버 시에도 밑줄 제거 */
    }
    
    /* 🔥 빨간색 포커스 스타일 제거 */
    .stButton > button:focus {
        outline: none !important;
        border: none !important;
        box-shadow: 0 0 0 2px rgba(0, 102, 204, 0.3) !important; /* 파란색 포커스로 변경 */
        text-decoration: none !important;
    }
    
    .stButton > button:active {
        outline: none !important;
        border: none !important;
        text-decoration: none !important;
    }
    
    /* Primary 버튼 */
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, var(--primary-color) 0%, #004499 100%);
        color: white;
    }
    
    .stButton > button[kind="primary"]:focus {
        background: linear-gradient(135deg, var(--primary-color) 0%, #004499 100%);
        color: white;
        outline: none !important;
        border: none !important;
        box-shadow: 0 0 0 2px rgba(0, 102, 204, 0.3) !important;
    }
    
    /* Secondary 버튼 */
    .stButton > button[kind="secondary"] {
        background: linear-gradient(145deg, var(--light-bg) 0%, #e9ecef 100%);
        color: #495057;
        border: 1px solid #dee2e6;
    }
    
    .stButton > button[kind="secondary"]:focus {
        background: linear-gradient(145deg, var(--light-bg) 0%, #e9ecef 100%);
        color: #495057;
        outline: none !important;
        border: 1px solid #dee2e6 !important;
        box-shadow: 0 0 0 2px rgba(108, 117, 125, 0.3) !important;
    }
    
    /* 🔥 진행률 바 개선 - 애니메이션 제거 */
    .progress-container {
        margin-bottom: 1rem;
    }
    
    .progress-label {
        font-size: 0.9rem;
        font-weight: 500;
        margin-bottom: 0.5rem;
        color: #495057;
    }
    
    .progress-bar-bg {
        width: 100%;
        height: 1rem;
        background-color: #e9ecef;
        border-radius: 10px;
        overflow: hidden;
        box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.1);
        position: relative;
    }

    .progress-bar-fill {
        height: 100%;
        border-radius: 10px;
        transition: width 0.6s cubic-bezier(.4,2,.6,1), background-color 0.4s;
        background: linear-gradient(to bottom, rgba(255,255,255,0.2), rgba(0,0,0,0.02));
        position: relative;
        overflow: hidden;
    }
    
    /* 🔥 익스팬더 스타일 개선 */
    .streamlit-expanderHeader {
        background: linear-gradient(90deg, var(--light-bg) 0%, #ffffff 100%);
        border-radius: var(--border-radius);
        padding: 0.8rem 1rem;
        border: 1px solid #e9ecef;
        margin-bottom: 0.5rem;
        transition: var(--transition);
    }
    
    .streamlit-expanderHeader:hover {
        background: linear-gradient(90deg, #e9ecef 0%, var(--light-bg) 100%);
        box-shadow: var(--shadow-soft);
    }
    
    /* 🔥 입력 필드 개선 */
    .stNumberInput > div > div > input,
    .stSelectbox > div > div > div,
    .stTextInput > div > div > input {
        border-radius: var(--border-radius);
        border: 2px solid #e9ecef;
        transition: border-color 0.3s ease;
        padding: 0.5rem 0.75rem;
    }
    
    .stNumberInput > div > div > input:focus,
    .stSelectbox > div > div > div:focus,
    .stTextInput > div > div > input:focus {
        border-color: var(--primary-color);
        box-shadow: 0 0 0 3px rgba(0, 102, 204, 0.1);
    }
    
    /* 🔥 성공/경고/에러 메시지 스타일링 */
    .stSuccess {
        background: linear-gradient(135deg, #d4edda 0%, #c3e6cb 100%);
        border-radius: var(--border-radius);
        border: none;
        box-shadow: var(--shadow-soft);
    }
    
    .stWarning {
        background: linear-gradient(135deg, #fff3cd 0%, #ffeaa7 100%);
        border-radius: var(--border-radius);
        border: none;
        box-shadow: var(--shadow-soft);
    }
    
    .stError {
        background: linear-gradient(135deg, #f8d7da 0%, #f5c6cb 100%);
        border-radius: var(--border-radius);
        border: none;
        box-shadow: var(--shadow-soft);
    }
    
    .stInfo {
        background: linear-gradient(135deg, #d1ecf1 0%, #bee5eb 100%);
        border-radius: var(--border-radius);
        border: none;
        box-shadow: var(--shadow-soft);
    }
    
    /* 🔥 DataEditor 스타일링 */
    .stDataFrame {
        border-radius: var(--border-radius);
        overflow: hidden;
        box-shadow: var(--shadow-soft);
    }
    
    /* 🔥 모바일 반응형 디자인 */
    @media (max-width: 768px) {
        .main .block-container {
            padding-left: 1rem;
            padding-right: 1rem;
        }
        
        [data-testid="metric-container"] {
            padding: 0.8rem;
        }
        
        .stTabs [data-baseweb="tab"] {
            padding: 0.5rem 0.75rem;
            font-size: 0.85rem !important;
            min-width: 80px;
        }
        
        .stButton > button {
            padding: 0.5rem 1rem;
            font-size: 0.9rem;
        }
        
        .progress-label {
            font-size: 0.8rem;
        }
        
        .progress-bar-bg {
            height: 0.8rem;
        }
    }
    
    @media (max-width: 480px) {
        .stTabs [data-baseweb="tab-list"] {
            justify-content: center;
        }
        
        .stTabs [data-baseweb="tab"] {
            flex: 1;
            text-align: center;
            min-width: 60px;
        }
    }
    </style>
    """, unsafe_allow_html=True)


###############################################################################
# 8. 페이지 설정 및 사이드바 (강화된 이수구분 관리)
###############################################################################

# 페이지 설정
st.set_page_config(
    page_title="KU 학점 계산기",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 데이터 무결성 관리자 초기화
integrity_manager = DataIntegrityManagerFixed()

# 🔥 강화된 조건부 데이터 검증 
should_skip_integrity = (
    st.session_state.get("skip_integrity_check", False) or
    st.session_state.get("csv_import_just_completed", False) or
    st.session_state.get("force_major_type_sync", False) or
    st.session_state.get("major_type_changing", False)
)

if not st.session_state.courses.empty and not should_skip_integrity:
    integrity_manager.validate_and_fix_all()

# 🔥 스킵 플래그들 리셋
if st.session_state.get("skip_integrity_check", False):
    st.session_state.skip_integrity_check = False
if st.session_state.get("csv_import_just_completed", False):
    st.session_state.csv_import_just_completed = False

# 완전한 CSS 적용
apply_custom_css()

# 사이드바 설정
with st.sidebar:
    st.header("⚙️ 빠른 설정")
    
    # 🔥 전공 유형 선택 - Widget 완전 재생성 방식 (오류 수정)
    with st.expander("🎯 전공 유형 설정", expanded=True):
        st.markdown("**복수/이중/심화 중 하나만 선택 가능합니다**")

        # 🔥 Widget 강제 재생성 처리 (수정된 버전)
        if st.session_state.get("force_widget_recreation", False):
            # CSV 가져오기 후 widget 완전 재생성
            new_major_type = st.session_state.get("new_major_type", "심화전공")

            # Widget 재생성을 위한 고유 키 생성
            widget_key = f"major_type_widget_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S_%f')}"

            major_type = st.selectbox(
                "전공 유형 선택",
                MAJOR_TYPE_OPTIONS,
                index=MAJOR_TYPE_OPTIONS.index(new_major_type),
                key=widget_key,
                on_change=update_major_type_with_validation,
                help="선택한 전공 유형에 따라 이수구분과 졸업 요건이 자동으로 설정됩니다"
            )

            # 🔥 수정: widget 생성 후 session_state 직접 할당 제거
            # 재생성 완료 후 플래그 정리만 수행
            st.session_state.force_widget_recreation = False
            if "new_major_type" in st.session_state:
                del st.session_state.new_major_type

            # 🔥 수정: major_type_widget 키만 업데이트 (widget key 자체는 할당하지 않음)
            st.session_state.major_type_widget = new_major_type

            st.success(f"🔄 **전공 유형 UI 업데이트 완료**: {new_major_type}")

        else:
            # 🔥 일반적인 경우: 기존 방식
            if "major_type_widget" not in st.session_state:
                st.session_state.major_type_widget = st.session_state.major_type

            try:
                current_index = MAJOR_TYPE_OPTIONS.index(st.session_state.major_type)
            except (ValueError, KeyError):
                current_index = 0
                st.session_state.major_type = MAJOR_TYPE_OPTIONS[0]

            major_type = st.selectbox(
                "전공 유형 선택",
                MAJOR_TYPE_OPTIONS,
                index=current_index,
                key="major_type_widget",
                on_change=update_major_type_with_validation,
                help="선택한 전공 유형에 따라 이수구분과 졸업 요건이 자동으로 설정됩니다"
            )

        # 전공 유형별 설명
        if major_type == "심화전공":
            st.info("📚 **심화전공**: 전공선택 초과분이 심화전공으로 자동 계산됩니다")
        elif major_type == "복수전공":
            st.info("🎓 **복수전공**: 본전공 + 복수전공 요건을 모두 충족해야 합니다")
        elif major_type == "이중전공":
            st.info("🎯 **이중전공**: 본전공 + 이중전공 요건을 모두 충족해야 합니다")
    
    # 🔥 새로운 이수구분 관리 (강화된 UI)
    with st.expander("🏷️ 이수구분 관리", expanded=False):
        st.markdown("**학과에 맞는 이수구분을 추가하거나 삭제하세요**")
        
        # 현재 이수구분 표시
        st.subheader("📋 현재 이수구분")
        current_cats = get_current_categories()
        
        # 기본/커스텀 구분해서 표시
        base_cats = get_categories_by_major_type(st.session_state.major_type)
        custom_cats = [cat for cat in current_cats if cat not in base_cats]
        
        st.markdown("**기본 이수구분:**")
        for i, cat in enumerate(base_cats):
            st.markdown(f"• {cat}")
        
        if custom_cats:
            st.markdown("**추가된 이수구분:**")
            for i, cat in enumerate(custom_cats):
                st.markdown(f"• {cat}")
        
        st.divider()
        
        # 이수구분 추가
        st.subheader("➕ 이수구분 추가")
        
        # 빠른 추가 (미리 정의된 옵션)
        st.markdown("**빠른 추가:**")
        available_predefined = [cat for cat in PREDEFINED_CATEGORIES if cat not in current_cats]
        
        if available_predefined:
            quick_add_cat = st.selectbox(
                "미리 정의된 이수구분",
                ["선택하세요"] + available_predefined,
                key="quick_add_select"
            )
            
            if st.button("⚡ 빠른 추가", use_container_width=True):
                if quick_add_cat != "선택하세요":
                    # 기본 학점 설정
                    default_credits = {"학문의기초": 12, "전공관련교양": 6, "경제학필수과목": 9, "교직": 22}.get(quick_add_cat, 6)
                    
                    st.session_state.custom_categories.append(quick_add_cat)
                    st.session_state.custom_requirements[quick_add_cat] = {"required": default_credits}
                    invalidate_cache()
                    st.success(f"✅ '{quick_add_cat}' 이수구분이 추가되었습니다! (기본 {default_credits}학점)")
                    st.rerun()
        else:
            st.info("📝 모든 미리 정의된 이수구분이 이미 추가되었습니다.")
        
        # 직접 입력
        st.markdown("**직접 입력:**")
        custom_cat_name = st.text_input(
            "새 이수구분 이름",
            placeholder="예: 전공관련교양, 경제학필수과목",
            max_chars=20,
            key="custom_category_input",
            help="한글, 영문, 숫자, 공백만 사용 가능 (최대 20자)"
        )
        
        if st.button("📝 직접 추가", use_container_width=True):
            if custom_cat_name:
                if add_custom_category(custom_cat_name):
                    st.rerun()
        
        st.divider()
        
        # 이수구분 삭제
        st.subheader("🗑️ 이수구분 삭제")
        deletable_cats = [cat for cat in current_cats if cat not in base_cats]
        
        if deletable_cats:
            delete_cat = st.selectbox(
                "삭제할 이수구분",
                ["선택하세요"] + deletable_cats,
                key="delete_category_select"
            )
            
            if st.button("🗑️ 삭제", use_container_width=True):
                if delete_cat != "선택하세요":
                    if remove_custom_category(delete_cat):
                        st.rerun()
        else:
            st.info("📝 삭제 가능한 커스텀 이수구분이 없습니다.")
    
    # 🔥 졸업 요건 설정 (커스텀 이수구분 포함)
    with st.expander("🎓 졸업 요건 설정", expanded=False):
        st.markdown("**현재 학과의 졸업 요건에 맞게 설정하세요**")
        
        # 기본 요건 설정
        st.subheader("📊 학점 요구사항")
        
        # 🔥 총 졸업 학점 자동 계산 (사용자 입력 불가)
        auto_total = auto_calculate_total_credits(st.session_state.custom_requirements)
        st.session_state.custom_requirements["총계"]["required"] = auto_total

        st.info(f"📊 **총 졸업학점**: {auto_total}학점 (자동 계산)")
        st.caption("💡 총 졸업학점은 각 영역별 요구학점의 합으로 자동 계산되며 수정할 수 없습니다.")

        # 각 영역별 학점 설정 (커스텀 이수구분 포함)
        st.subheader("📋 영역별 요구 학점")
        
        # 졸업 요건 카테고리 (심화전공 + 커스텀 이수구분 포함)
        requirements_categories = get_requirements_categories_by_major_type(st.session_state.major_type)
        
        # 커스텀 이수구분도 추가
        custom_cats = [cat for cat in get_current_categories() if cat not in requirements_categories]
        requirements_categories.extend(custom_cats)
        
        for category in requirements_categories:
            if category == "총계":
                continue  # 총계는 자동 계산으로 처리
            
            current_value = st.session_state.custom_requirements.get(category, {"required": 0})["required"]
            widget_key = f"req_{category}_widget"

            # 카테고리별 설명
            help_text = f"{category} 영역에서 이수해야 하는 최소 학점"
            if category == "심화전공":
                help_text = "심화전공 요구학점 설정 (전공선택 초과분으로 자동 계산됨)"

            st.number_input(
                f"{category} 요구학점",
                min_value=0,
                max_value=100,
                value=current_value,
                step=1,
                key=widget_key,
                on_change=update_requirement_with_auto_total(category),  # ✅ 수정된 콜백 함수
                help=help_text
            )

        st.divider()
        
        # 요건 초기화
        if st.button("🔄 기본 요건으로 초기화", use_container_width=True):
            st.session_state.custom_requirements = get_requirements_by_major_type(st.session_state.major_type)
            invalidate_cache()
            st.success("✅ 기본 졸업 요건으로 초기화되었습니다!")
            st.rerun()
    
    # 기본값 설정
    with st.expander("🔧 기본값 설정", expanded=False):
        default_credit = st.selectbox("기본 학점", CREDIT_OPTIONS, index=2)  # 3.0이 기본값
        default_grade = st.selectbox("기본 성적", GRADE_OPTIONS, index=1)
        default_category = st.selectbox("기본 이수구분", get_current_categories())
    
    st.divider()
    
    # 데이터 관리
    st.header("📁 데이터 관리")
    
    # 백업/복원 버튼
    col1, col2 = st.columns(2)
    with col1:
        if st.button("💾 백업", use_container_width=True):
            backup_data_enhanced()
    with col2:
        if st.button("↩️ 복원", use_container_width=True):
            restore_data_enhanced()
    
    st.markdown("---")
    
    # CSV 내보내기
    st.subheader("📤 내보내기")
    if st.button("CSV 생성", use_container_width=True):
        csv = export_to_csv_with_metadata()
        if csv:
            st.download_button(
                label="💾 CSV 다운로드 (메타데이터 포함)",
                data=csv,
                file_name=f"KU_성적_Enhanced_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
                use_container_width=True
            )
    
    # CSV 가져오기
    st.subheader("📥 가져오기")
    uploaded = st.file_uploader("CSV 파일 선택 (메타데이터 지원)", type="csv")
    if uploaded and st.button("📥 가져오기 실행", use_container_width=True):
        import_from_csv_with_metadata(uploaded)
    
###############################################################################
# 9. 메인 UI — 과목 입력 테이블 (커스텀 이수구분 지원) + 대시보드
###############################################################################

# 개선된 제목
st.title("🎓 고려대학교 GPA 계산기")
st.markdown("""
<div style='text-align: center; margin-bottom: 2rem;'>
    <p style='font-size: 1.1rem; color: #6c757d; margin: 0;'>
    </p>
</div>
            
""", unsafe_allow_html=True)

# 🎨 대시보드 스타일 메인 화면
render_dashboard()

# 🎨 스마트 입력 도우미
render_smart_input_helper()

# 🎨 강화된 사용자 가이드 (새로 추가된 위치)
render_enhanced_user_guide()

# 행 관리 버튼
st.subheader("📝 과목 입력")

button_cols = st.columns([1, 1, 3])

with button_cols[0]:
    if st.button("➕ 행 추가", key="add_row", use_container_width=True, type="primary"):
        _add_row()

with button_cols[1]:
    if st.button("🗑️ 마지막 행 삭제", key="del_row", use_container_width=True, type="secondary"):
        _del_row()

with button_cols[2]:
    st.empty()

st.markdown("---")

# DataEditor (커스텀 이수구분 지원) - 연도 기본값 설정
dynamic_height = calculate_data_editor_height(st.session_state.courses)

edited_df = st.data_editor(
    st.session_state.courses,
    key="courses_editor",
    on_change=update_courses,
    column_config={
        "과목명": st.column_config.TextColumn(
            "과목명",
            help="과목명을 입력하세요",
            max_chars=50,
        ),
        "학점": st.column_config.SelectboxColumn(
            "학점",
            help="학점을 선택하세요 (3, 2, 1학점만 가능)",
            options=CREDIT_OPTIONS,  # 이미 [3.0, 2.0, 1.0] 순서
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
            help="이수구분을 선택하세요 (사이드바에서 추가 가능)",
            options=get_cached_categories(),
            required=True
        ),
        "연도": st.column_config.SelectboxColumn(
            "연도",
            help="수강 연도를 선택하세요 (현재 연도가 상단에 표시됨)",
            options=YEAR_OPTIONS,  # 이미 역순으로 정렬됨 (2035~2015)
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
    num_rows="dynamic",
    use_container_width=True,
    hide_index=True,
    height=dynamic_height
)

###############################################################################
# 10. 수정된 GPA/학점 계산 함수 (커스텀 이수구분 초과분 처리)
###############################################################################

@st.cache_data
def calculate_cached(df_hash: str, df_raw: pd.DataFrame, req_hash: str) -> Tuple[pd.DataFrame, Dict[str, float], Dict[str, float]]:
    """해시값을 이용한 캐시된 계산 (성능 최적화)"""
    return calculate_with_overflow(df_raw)

def calculate_with_overflow(df_raw: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, float], Dict[str, float]]:
    """🔥 커스텀 이수구분 지원 계산 함수: 초과분 처리 로직 포함"""
    df = df_raw.copy()
    df["학점"] = pd.to_numeric(df["학점"], errors="coerce").fillna(0.0)
    df["연도"] = pd.to_numeric(df["연도"], errors="coerce").fillna(0).astype(int)

    # 재수강 중복 제거
    deduped = (
        df.sort_values("재수강")
        .drop_duplicates(subset=["과목명"], keep="last")
    )

    # 전체 GPA 계산
    gpa_rows = deduped[deduped["성적"].map(GRADE_MAP_45).notna()].copy()
    gpa_rows["평점"] = gpa_rows["성적"].map(GRADE_MAP_45)

    total_points = (gpa_rows["학점"] * gpa_rows["평점"]).sum()
    total_credits_gpa = gpa_rows["학점"].sum()
    overall_gpa = total_points / total_credits_gpa if total_credits_gpa else 0.0

    # 🔥 1단계: 기본 이수학점 계산 (모든 이수구분 포함)
    raw_summary = {}
    all_categories = get_current_categories()
    
    for cat in all_categories:
        cat_rows = deduped[deduped["이수구분"] == cat]
        raw_summary[cat] = cat_rows["학점"].sum()

    # 🔥 2단계: 심화전공 자동 계산 (전공선택 초과분)
    current_requirements = get_current_requirements()
    
    # 전공선택 초과분 → 심화전공 (심화전공 선택 시에만)
    major_selection_credits = raw_summary.get("전공선택", 0)
    major_selection_required = current_requirements.get("전공선택", {"required": 24})["required"]
    
    if st.session_state.major_type == "심화전공":
        if major_selection_credits > major_selection_required:
            overflow_to_advanced = major_selection_credits - major_selection_required
            raw_summary["전공선택"] = major_selection_required
            raw_summary["심화전공"] = overflow_to_advanced
        else:
            raw_summary["심화전공"] = 0

    # 🔥 3단계: 모든 이수구분 초과분 → 일반선택으로 이동
    adjusted_summary = {}
    total_overflow = 0
    
    # 요구학점 설정에 있는 모든 카테고리 순회 (커스텀 이수구분 포함)
    all_requirement_categories = list(current_requirements.keys())
    if "총계" in all_requirement_categories:
        all_requirement_categories.remove("총계")
    
    for cat in all_requirement_categories:
        if cat == "일반선택":
            continue  # 일반선택은 나중에 처리
        
        earned = raw_summary.get(cat, 0)
        required = current_requirements.get(cat, {"required": 0})["required"]
        
        if earned > required:
            # 초과분 계산
            overflow = earned - required
            total_overflow += overflow
            adjusted_summary[cat] = required  # 요구량만 기록
        else:
            adjusted_summary[cat] = earned

    # 일반선택 = 기본 일반선택 과목 + 모든 초과분
    base_general = raw_summary.get("일반선택", 0)
    adjusted_summary["일반선택"] = base_general + total_overflow

    # 🔥 4단계: 진행률 데이터프레임 생성 (커스텀 이수구분 포함)
    summary_records = []
    for cat in all_requirement_categories:
        # 심화전공은 심화전공 선택 시에만 표시
        if cat == "심화전공" and st.session_state.major_type != "심화전공":
            continue
        
        earned = adjusted_summary.get(cat, 0)
        required = current_requirements.get(cat, {"required": 0})["required"]
        
        # GPA 계산 (커스텀 이수구분 포함)
        if cat == "심화전공" and st.session_state.major_type == "심화전공":
            # 심화전공 GPA는 전공선택 과목들의 GPA
            major_selection_rows = deduped[deduped["이수구분"] == "전공선택"]
            major_gpa_rows = major_selection_rows[major_selection_rows["성적"].map(GRADE_MAP_45).notna()]
            if not major_gpa_rows.empty:
                major_points = (major_gpa_rows["학점"] * major_gpa_rows["성적"].map(GRADE_MAP_45)).sum()
                major_credits = major_gpa_rows["학점"].sum()
                cat_gpa = major_points / major_credits if major_credits else np.nan
            else:
                cat_gpa = np.nan
        else:
            # 일반적인 카테고리 GPA 계산 (커스텀 이수구분 포함)
            cat_rows = deduped[deduped["이수구분"] == cat]
            cat_gpa_rows = cat_rows[cat_rows["성적"].map(GRADE_MAP_45).notna()]
            if not cat_gpa_rows.empty:
                cat_points = (cat_gpa_rows["학점"] * cat_gpa_rows["성적"].map(GRADE_MAP_45)).sum()
                cat_gpa_credits = cat_gpa_rows["학점"].sum()
                cat_gpa = cat_points / cat_gpa_credits if cat_gpa_credits else np.nan
            else:
                cat_gpa = np.nan

        summary_records.append((cat, earned, cat_gpa))

    summary_df = pd.DataFrame(summary_records, columns=["영역", "이수학점", "평균 GPA"])

    # 🔥 5단계: 초과 이수학점 계산
    total_earned = sum(adjusted_summary.values())
    total_required = current_requirements["총계"]["required"]
    
    # 모든 진행률이 100% 달성되었는지 확인
    all_requirements_met = True
    for cat in all_requirement_categories:
        if cat == "심화전공" and st.session_state.major_type != "심화전공":
            continue
        earned = adjusted_summary.get(cat, 0)
        required = current_requirements.get(cat, {"required": 0})["required"]
        if earned < required:
            all_requirements_met = False
            break

    excess_credits = 0
    if all_requirements_met and total_earned > total_required:
        excess_credits = total_earned - total_required

    misc = {
        "overall_gpa": round(overall_gpa, 2),
        "earned_credits": total_earned,
        "gpa_credits": total_credits_gpa,
        "excess_credits": excess_credits,
        "all_requirements_met": all_requirements_met
    }
    
    overflow_info = {
        "total_overflow": total_overflow,
        "raw_summary": raw_summary,
        "adjusted_summary": adjusted_summary
    }
    
    return summary_df, misc, overflow_info

###############################################################################
# 11. 계산 & 결과 표시 (데이터 검증 포함)
###############################################################################

st.markdown("---")

# 계산 버튼 (데이터 검증 포함)
col1, col2, col3 = st.columns([2, 1, 2])
with col2:
    if st.button("📊 계 산 하 기", type="primary", use_container_width=True):
        # 🔥 계산 전 데이터 검증
        integrity_manager.validate_and_fix_all()
        calculate_button_pressed = True
    else:
        calculate_button_pressed = False

# 🔥 계산 버튼 부분 수정
if calculate_button_pressed:
    df_courses = st.session_state.courses.copy()
    
    # 🔥 개선된 데이터 검증
    if df_courses.empty:
        st.error("⚠️ 과목 데이터가 없습니다!")
        st.stop()
    
    # 데이터 품질 검증 및 보고서
    issues, fixes = validate_course_data(df_courses)
    if issues:
        st.warning("⚠️ 데이터 품질 문제가 발견되어 자동으로 수정되었습니다:")
        for issue, fix in zip(issues, fixes):
            col1, col2 = st.columns([1, 1])
            with col1:
                st.error(f"• {issue}")
            with col2:
                st.success(f"• {fix}")
    
    # 유효한 과목만 필터링
    valid_courses = get_valid_courses(df_courses)
    if valid_courses.empty:
        st.error("⚠️ 유효한 과목 데이터가 없습니다!")
        st.info("💡 **해결 방법**: 과목명, 학점, 성적 등을 올바르게 입력하세요.")
        st.stop()
    
    # 데이터 품질 보고서 표시
    show_data_quality_report(df_courses)
    
    # 캐시된 계산 사용
    try:
        df_hash = str(hash(valid_courses.to_string()))
    except:
        df_hash = str(hash(str(valid_courses.values.tolist())))

    req_hash = str(hash(str(get_cached_requirements())))
    summary_df, misc, overflow_info = calculate_cached(df_hash, valid_courses, req_hash)
    st.session_state.calculation_results = (summary_df, misc, overflow_info)

# 계산 결과 표시
if st.session_state.calculation_results is not None:
    summary_df, misc, overflow_info = st.session_state.calculation_results
    current_requirements = get_current_requirements()

    st.markdown("---")
    st.subheader("✅ 누적 결과")
    
    # 메트릭 표시
    metric_cols = st.columns(4)
    with metric_cols[0]:
        st.metric(
            label="🎯 전체 평균 GPA (4.5)",
            value=f"{misc['overall_gpa']:.2f}",
            delta=f"{misc['overall_gpa'] - 3.0:.2f}" if misc['overall_gpa'] >= 3.0 else None
        )
    with metric_cols[1]:
        total_required = current_requirements["총계"]["required"]
        st.metric(
            label="📚 총 이수 학점",
            value=f"{misc['earned_credits']:.0f} 학점",
            delta=f"{misc['earned_credits'] - total_required:.0f}" if misc['earned_credits'] >= total_required else None
        )
    with metric_cols[2]:
        st.metric(
            label="📊 GPA 반영 학점",
            value=f"{misc['gpa_credits']:.0f} 학점"
        )
    with metric_cols[3]:
        # 초과 이수학점 표시
        if misc['excess_credits'] > 0:
            st.metric(
                label="🎉 초과 이수학점",
                value=f"+{misc['excess_credits']:.0f} 학점",
                delta="졸업 요건 초과 달성",
                delta_color="normal"
            )
        else:
            st.metric(
                label="⏳ 남은 학점",
                value=f"{total_required - misc['earned_credits']:.0f} 학점",
                delta="졸업까지 필요"
            )

    # 초과분 처리 정보 표시
    if overflow_info['total_overflow'] > 0:
        with st.expander("📊 초과분 처리 상세"):
            st.markdown(f"**총 초과분**: {overflow_info['total_overflow']:.0f}학점이 일반선택으로 이동되었습니다.")
            
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**원본 이수학점**")
                raw_df = pd.DataFrame(list(overflow_info['raw_summary'].items()), columns=['영역', '원본학점'])
                st.dataframe(raw_df, hide_index=True)
            
            with col2:
                st.markdown("**조정된 이수학점**")
                adj_df = pd.DataFrame(list(overflow_info['adjusted_summary'].items()), columns=['영역', '조정학점'])
                st.dataframe(adj_df, hide_index=True)

    # 영역별 결과 표
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

    render_enhanced_progress_with_guidance(summary_df, current_requirements, misc)

    # 목표 GPA 시뮬레이션 (수정된 버전)
    with st.expander("🎯 목표 GPA 시뮬레이션", expanded=True):
        target_cols = st.columns([1, 2])
        
        with target_cols[0]:
            target = st.number_input(
                "목표 졸업 GPA", 
                min_value=0.0, 
                max_value=4.5, 
                value=st.session_state.target_gpa,
                step=0.1,
                key="target_gpa_widget",
                on_change=update_target_gpa
            )
        
        with target_cols[1]:
            remain = total_required - misc["earned_credits"]
            if remain <= 0:
                if misc['excess_credits'] > 0:
                    st.success(f"🎉 졸업 학점을 {misc['excess_credits']:.0f}학점 초과 달성했습니다!")
                else:
                    st.success("🎉 졸업 학점을 정확히 충족했습니다!")
            else:
                # 🔥 수정된 목표 GPA 계산 로직
                current_total_points = misc["overall_gpa"] * misc["gpa_credits"]
                future_total_credits = misc["gpa_credits"] + remain
                target_total_points = st.session_state.target_gpa * future_total_credits
                needed_points = target_total_points - current_total_points
                
                if remain > 0:
                    need_avg = needed_points / remain
                else:
                    need_avg = 0
                
                # 음수 체크 (이미 목표 달성한 경우)
                if need_avg < 0:
                    st.success(f"🎉 이미 목표 GPA {st.session_state.target_gpa:.2f}를 달성했습니다!")
                elif need_avg > 4.5:
                    st.warning("⚠️ 남은 학점에서 목표 GPA 달성이 불가능합니다.")
                    # 달성 가능한 최대 GPA 계산
                    max_possible_points = current_total_points + (remain * 4.5)
                    max_possible_gpa = max_possible_points / future_total_credits
                    st.info(f"💡 달성 가능한 최대 GPA: **{max_possible_gpa:.2f}**")
                else:
                    # 성적 등급으로 변환해서 표시
                    grade_guide = ""
                    if need_avg >= 4.25:
                        grade_guide = " (A+ 필요)"
                    elif need_avg >= 3.75:
                        grade_guide = " (A+ ~ A0)"
                    elif need_avg >= 3.25:
                        grade_guide = " (A0 ~ B+)"
                    elif need_avg >= 2.75:
                        grade_guide = " (B+ ~ B0)"
                    else:
                        grade_guide = " (B0 이하 가능)"
                    
                    st.info(f"📝 남은 **{remain:.0f}학점**에서 평균 **{need_avg:.2f}** 이상 받아야 합니다{grade_guide}")

    # 🌟 상세 통계 및 분석 기능 (문제점 해결 버전)
    with st.expander("📈 상세 통계 및 분석", expanded=True):
        df_courses = st.session_state.courses.copy()
        
        # 🔥 안전한 필터링
        try:
            if "과목명" in df_courses.columns and not df_courses.empty:
                df_courses["과목명"] = df_courses["과목명"].fillna("").astype(str)
                valid_courses = df_courses[df_courses["과목명"].str.strip() != ""]
            else:
                valid_courses = pd.DataFrame()
        except Exception as e:
            st.warning(f"통계 데이터 처리 중 오류: {str(e)}")
            valid_courses = pd.DataFrame()
        
        if not valid_courses.empty:
            # 재수강 중복 제거
            deduped_for_stats = (
                valid_courses.sort_values("재수강")
                .drop_duplicates(subset=["과목명"], keep="last")
            )

            # 동적 전공 과목 필터링
            major_categories = get_major_categories()
            major_courses = deduped_for_stats[deduped_for_stats["이수구분"].isin(major_categories)]

            # 개선된 탭
            stats_tabs = st.tabs(["📊 학기별 추이", "🎓 전공 과목 분석", "🎯 성적 분포", "📚 이수구분별", "📅 연도별 학점"])

            def calculate_y_axis_range(gpa_values):
                """GPA 값들을 기반으로 스마트 Y축 범위 계산"""
                if not gpa_values or len(gpa_values) == 0:
                    return [0, 4.5]  # 기본 범위

                min_gpa = min(gpa_values)
                max_gpa = max(gpa_values)
                gpa_range = max_gpa - min_gpa

                # 아웃라이어 판정: 범위가 1.5 이상이거나 최소값이 2.0 미만
                if gpa_range >= 1.5 or min_gpa < 2.0:
                    return [0, 4.5]  # 전체 범위
                else:
                    # 여백을 두고 범위 조정 (0.3씩 여백)
                    margin = 0.3
                    y_min = max(0, min_gpa - margin)
                    y_max = min(4.5, max_gpa + margin)

                    # 최소 범위 보장 (1.0 이상)
                    if y_max - y_min < 1.0:
                        center = (y_min + y_max) / 2
                        y_min = max(0, center - 0.5)
                        y_max = min(4.5, center + 0.5)

                    return [y_min, y_max]

            with stats_tabs[0]:
                st.subheader("📊 학기별 GPA 추이")

                # 🔥 문제 해결: 간단한 체크박스 처리 (콜백 제거)
                control_cols = st.columns(2)

                with control_cols[0]:
                    show_overall = st.checkbox(
                        "📊 전체 GPA", 
                        value=True,  # 기본값 True
                        key="show_overall_simple",
                        help="전체 과목의 GPA 추이를 표시합니다"
                    )

                with control_cols[1]:
                    show_major = st.checkbox(
                        "🎓 전공 GPA", 
                        value=True,  # 기본값 True
                        key="show_major_simple",
                        help="전공 과목의 GPA 추이를 표시합니다"
                    )

                # 🔥 문제 해결: 둘 다 비활성화시 경고 한번만 표시하고 조기 종료
                if not show_overall and not show_major:
                    st.warning("⚠️ 최소 하나의 GPA 유형을 선택하세요.")
                else:
                    # 학기별 통계 계산
                    semester_stats = []
                    for (year, term), group in deduped_for_stats.groupby(['연도', '학기']):
                        # 전체 GPA 계산
                        gpa_rows = group[group["성적"].map(GRADE_MAP_45).notna()]
                        if not gpa_rows.empty:
                            total_credits = group["학점"].sum()
                            gpa_credits = gpa_rows["학점"].sum()
                            semester_gpa = (gpa_rows["학점"] * gpa_rows["성적"].map(GRADE_MAP_45)).sum() / gpa_credits

                            # 동적 전공 GPA 계산
                            major_group = group[group["이수구분"].isin(major_categories)]
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
                                '학기GPA': round(semester_gpa, 2),
                                '전공GPA': major_gpa
                            })

                    if semester_stats:
                        semester_df = pd.DataFrame(semester_stats)

                        if len(semester_df) > 1:
                            semester_df['학기_순서'] = semester_df['연도'].astype(str) + '-' + semester_df['학기']

                            # 🔥 선택된 GPA 유형에 따라 차트 데이터 구성
                            chart_data = []
                            all_gpa_values = []  # Y축 범위 계산용

                            for _, row in semester_df.iterrows():
                                # 전체 GPA 추가
                                if show_overall:
                                    chart_data.append({
                                        '학기_순서': row['학기_순서'],
                                        'GPA': row['학기GPA'],
                                        '구분': '전체 GPA'
                                    })
                                    all_gpa_values.append(row['학기GPA'])

                                # 전공 GPA 추가 (데이터가 있는 경우만)
                                if show_major and row['전공GPA'] is not None:
                                    chart_data.append({
                                        '학기_순서': row['학기_순서'],
                                        'GPA': row['전공GPA'],
                                        '구분': '전공 GPA'
                                    })
                                    all_gpa_values.append(row['전공GPA'])

                            chart_df = pd.DataFrame(chart_data)

                            if not chart_df.empty:
                                # 🔥 스마트 Y축 범위 계산
                                y_range = calculate_y_axis_range(all_gpa_values)

                                # 🔥 색상 설정 (선택된 GPA 유형에 따라)
                                if show_overall and show_major:
                                    color_range = [CHART_COLORS['primary'], CHART_COLORS['secondary']]
                                elif show_overall:
                                    color_range = [CHART_COLORS['primary']]
                                else:  # show_major만
                                    color_range = [CHART_COLORS['secondary']]

                                # 차트 생성
                                chart = alt.Chart(chart_df).mark_line(
                                    point=alt.OverlayMarkDef(filled=True, size=80),
                                    strokeWidth=3
                                ).encode(
                                    x=alt.X('학기_순서:O', 
                                           axis=alt.Axis(labelAngle=0, title="학기", 
                                                        titleFontSize=14, labelFontSize=12)),
                                    y=alt.Y('GPA:Q', 
                                           axis=alt.Axis(title="GPA", 
                                                        titleFontSize=14, labelFontSize=12),
                                           scale=alt.Scale(domain=y_range)),  # 🔥 스마트 Y축 범위 적용
                                    color=alt.Color('구분:N', 
                                                  scale=alt.Scale(range=color_range),
                                                  legend=alt.Legend(title="구분", titleFontSize=12, labelFontSize=11))
                                ).properties(
                                    height=400,
                                    title=alt.TitleParams(
                                        text="학기별 GPA 변화 추이",
                                        fontSize=16,
                                        fontWeight='bold',
                                        anchor="start"
                                    )
                                ).resolve_scale(
                                    color='independent'
                                )
                                st.altair_chart(chart, use_container_width=True)
                            else:
                                st.warning("⚠️ 선택한 GPA 유형에 해당하는 데이터가 없습니다.")
                        else:
                            st.info("📝 여러 학기 데이터가 있어야 추이를 확인할 수 있습니다.")

                        # 학기별 데이터 테이블 (필터링 없이 전체 표시)
                        st.subheader("📋 학기별 상세 데이터")
                        st.dataframe(semester_df, hide_index=True, use_container_width=True)
                    else:
                        st.info("📝 GPA 산정이 가능한 학기별 데이터가 없습니다.")

            with stats_tabs[1]:
                # 동적 전공 과목 분석
                major_category_str = " + ".join(major_categories)
                st.subheader(f"🎓 전공 과목 분석 ({major_category_str})")

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
                            # 동적 전공 요건으로 달성률 계산
                            total_major_required = sum([
                                current_requirements.get(cat, {"required": 0})["required"] 
                                for cat in major_categories
                            ])
                            if total_major_required > 0:
                                achievement_rate = (major_courses['학점'].sum() / total_major_required * 100)
                                st.metric("🎯 전공 요건 달성률", f"{achievement_rate:.1f}%")
                            else:
                                st.metric("🎯 전공 요건 달성률", "설정 필요")

                        # 전공 카테고리별 분포
                        if len(major_categories) > 1:
                            st.subheader("📊 전공 카테고리별 분포")
                            major_category_dist = major_courses.groupby('이수구분')['학점'].sum()
                            if not major_category_dist.empty:
                                cat_df = major_category_dist.reset_index()
                                cat_df.columns = ['카테고리', '학점']

                                chart = alt.Chart(cat_df).mark_bar(
                                    color=CHART_COLORS['info'],
                                    opacity=0.8
                                ).encode(
                                    x=alt.X('카테고리:O', 
                                           axis=alt.Axis(labelAngle=0, title="전공 카테고리",
                                                        titleFontSize=14, labelFontSize=12)),
                                    y=alt.Y('학점:Q', 
                                           axis=alt.Axis(title="학점",
                                                        titleFontSize=14, labelFontSize=12))
                                ).properties(
                                    height=350,
                                    title=alt.TitleParams(
                                        text="전공 카테고리별 학점 분포",
                                        fontSize=16,
                                        fontWeight='bold'
                                    )
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
                    grade_df = grade_dist.reset_index()
                    grade_df.columns = ['성적', '개수']

                    # 성적별 색상 매핑
                    grade_colors = {
                        'A+': CHART_COLORS['success'], 'A0': '#20c997',
                        'B+': CHART_COLORS['info'], 'B0': '#17a2b8',
                        'C+': CHART_COLORS['warning'], 'C0': '#ffc107',
                        'D+': CHART_COLORS['danger'], 'D0': '#dc3545',
                        'F': CHART_COLORS['dark'], 'P': CHART_COLORS['light'], 'NP': '#6c757d'
                    }

                    chart = alt.Chart(grade_df).mark_bar().encode(
                        x=alt.X('성적:O', 
                               axis=alt.Axis(labelAngle=0, title="성적",
                                            titleFontSize=14, labelFontSize=12)),
                        y=alt.Y('개수:Q', 
                               axis=alt.Axis(title="과목 수",
                                            titleFontSize=14, labelFontSize=12)),
                        color=alt.Color('성적:N',
                                      scale=alt.Scale(
                                          domain=list(grade_colors.keys()),
                                          range=list(grade_colors.values())
                                      ),
                                      legend=None)
                    ).properties(
                        height=400,
                        title=alt.TitleParams(
                            text="전체 과목 성적 분포",
                            fontSize=16,
                            fontWeight='bold'
                        )
                    )
                    st.altair_chart(chart, use_container_width=True)

            with stats_tabs[3]:
                st.subheader("📚 이수구분별 상세")
                category_stats = deduped_for_stats.groupby('이수구분').agg({
                    '학점': 'sum',
                    '과목명': 'count'
                }).rename(columns={'과목명': '과목수'})

                # 요구사항 대비 달성률 추가
                category_stats['요구학점'] = category_stats.index.map(
                    lambda x: current_requirements.get(x, {"required": 0})["required"]
                )
                category_stats['달성률(%)'] = (
                    category_stats['학점'] / category_stats['요구학점'] * 100
                ).round(1)

                st.dataframe(category_stats, use_container_width=True)

            with stats_tabs[4]:
                st.subheader("📅 연도별 학점 추이")
                yearly_credits = deduped_for_stats.groupby('연도')['학점'].sum()
                if len(yearly_credits) > 1:
                    yearly_df = yearly_credits.reset_index()
                    yearly_df.columns = ['연도', '학점']

                    chart = alt.Chart(yearly_df).mark_bar(
                        color=CHART_COLORS['primary'],
                        opacity=0.8
                    ).encode(
                        x=alt.X('연도:O', 
                               axis=alt.Axis(labelAngle=0, title="연도",
                                            titleFontSize=14, labelFontSize=12)),
                        y=alt.Y('학점:Q', 
                               axis=alt.Axis(title="학점",
                                            titleFontSize=14, labelFontSize=12))
                    ).properties(
                        height=400,
                        title=alt.TitleParams(
                            text="연도별 학점 취득 현황",
                            fontSize=16,
                            fontWeight='bold'
                        )
                    )
                    st.altair_chart(chart, use_container_width=True)
        else:
            st.info("📝 분석할 과목 데이터가 없습니다. 과목을 입력해주세요.")

###############################################################################
# 12. 학기별 조회 (기능 유지) - 오류 수정
###############################################################################

st.divider()
st.subheader("🔍 학기별 조회")

df_courses = st.session_state.courses.copy()
if df_courses.empty:
    st.info("📝 과목을 입력하면 학기별 조회 기능을 사용할 수 있습니다.")
else:
    # 🔥 안전한 데이터 처리
    try:
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
            s_df, s_misc, _ = calculate_with_overflow(filtered)
            
            # 선택 구간 결과를 메트릭으로 표시
            result_cols = st.columns([1, 1, 1])
            with result_cols[0]:
                st.metric("선택 구간 GPA", f"{s_misc['overall_gpa']:.2f}")
            with result_cols[1]:
                st.metric("선택 구간 학점", f"{s_misc['earned_credits']:.0f}")
            with result_cols[2]:
                # 🔥 안전한 과목 수 계산
                try:
                    if "과목명" in filtered.columns:
                        filtered["과목명"] = filtered["과목명"].fillna("").astype(str)
                        course_count = len(filtered[filtered["과목명"].str.strip() != ""])
                    else:
                        course_count = 0
                except Exception:
                    course_count = len(filtered)
                
                st.metric("과목 수", course_count)
            
            st.dataframe(filtered, use_container_width=True, hide_index=True)
    
    except Exception as e:
        st.error(f"학기별 조회 중 오류가 발생했습니다: {str(e)}")
        st.info("💡 페이지를 새로고침하거나 데이터를 확인해주세요.")

###############################################################################
# 13. 수정된 푸터
###############################################################################

st.divider()
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #666; font-size: 0.9rem; padding: 1rem;'>
🔒 모든 데이터는 브라우저에만 저장되며 외부로 전송되지 않습니다<br>
✨ 고려대학교 재학생 지원
</div>
""", unsafe_allow_html=True)
