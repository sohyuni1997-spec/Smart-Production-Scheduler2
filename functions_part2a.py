"""
생산 계획 하이브리드 시스템 - 핵심 함수 Part 2A
4단계: 물리 제약 정리
5단계: AI 전략 수립 (전반부)
"""

import google.generativeai as genai
import re
import json

# ==================== [4단계] 물리 제약 정보 정리 ====================
def step4_prepare_constraint_info(items_with_slack, target_line):
    """
    4단계: 물리 제약 정보 정리
    
    물리 제약:
    - T6: 조립1, 2, 3 모두 가능
    - A2XX: 조립1, 2만 가능 (조립3 금지)
    - 전용 모델: 동일 라인만 가능
    """
    
    constraint_info = []
    
    for item in items_with_slack:
        if not item['movable']:
            continue
        
        is_t6 = "T6" in item['name'].upper()
        is_a2xx = "A2XX" in item['name'].upper()
        
        if is_t6:
            possible_lines = [l for l in ["조립1", "조립2", "조립3"] if l != target_line]
            constraint = "조립1, 2, 3 모두 가능"
            priority = "조립3 우선 (라인 분산)"
        elif is_a2xx:
            possible_lines = [l for l in ["조립1", "조립2"] if l != target_line]
            constraint = "조립1, 2만 가능 (조립3 절대 금지)"
            priority = "조립2 이송"
        else:
            possible_lines = []
            constraint = f"{target_line} 내 날짜 이동만 가능"
            priority = "동일라인 연기"
        
        constraint_info.append({
            'name': item['name'],
            'qty_1차': item['qty_1차'],
            'plt': item['plt'],
            'max_movable': item['max_movable'],
            'buffer_days': item['buffer_days'],
            'constraint': constraint,
            'possible_lines': possible_lines,
            'priority': priority,
            'is_t6': is_t6,
            'is_a2xx': is_a2xx
        })
    
    return constraint_info

# ==================== [5단계] AI 전략 수립 - Part A ====================
def build_ai_fact_report(constraint_info, capa_status, target_date, target_line, reduction_needed):
    """AI에게 전달할 팩트 보고서 생성"""
    
    fact_report = f"""
### 📊 Python 수사 완료 (검증된 팩트)

**목표**: {target_date} {target_line}의 생산량을 {reduction_needed:,}개 감축

**이동 가능 품목 목록** (누적 납기 여유 검증 완료):
"""
    
    for idx, item in enumerate(constraint_info, 1):
        fact_report += f"""
{idx}. **{item['name']}**
   - 현재 수량: {item['qty_1차']:,}개
   - 이동 가능 최대: {item['max_movable']:,}개
   - PLT 단위: {item['plt']}개
   - 납기 여유: {item['buffer_days']}일
   - 물리 제약: {item['constraint']}
   - 추천: {item['priority']}
"""
    
    fact_report += f"""

**목적지 CAPA 현황:**
"""
    
    for key, status in capa_status.items():
        fact_report += f"- {status['date']} {status['line']}: 잔여 {status['remaining']:,}개 (가동률: {status['usage_rate']:.1f}%)\n"
    
    return fact_report
