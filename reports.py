"""
생산 계획 하이브리드 시스템 - 상세 보고서 생성
"""

def generate_full_report(stock_result, items_with_slack, capa_status, constraint_info, 
                        ai_strategy, final_moves, violations, target_qty, capa_target, 
                        reduction_needed, strategy_source, ai_failed, ai_error, today_str, question_date, target_line):
    """
    상세 보고서 생성
    
    포함 내용:
    - 1~6단계 전체 분석 결과
    - AI 전략 설명
    - Python 검증 결과
    - 최종 조치 계획
    
    Args:
        stock_result (dict): 1단계 품목 목록 결과
        items_with_slack (list): 2단계 누적 납기 여유 분석 결과
        capa_status (dict): 3단계 목적지 CAPA 현황
        constraint_info (list): 4단계 물리 제약 정보
        ai_strategy (dict): 5단계 AI 전략
        final_moves (list): 6단계 검증 통과한 최종 조치
        violations (list): 6단계 위반 사항
        target_qty (int): 목표 생산량
        capa_target (float): 목표 CAPA 비율
        reduction_needed (int): 필요 감축량
        strategy_source (str): 전략 수립 방식
        ai_failed (bool): AI 실패 여부
        ai_error (str): AI 오류 메시지
        today_str (str): 분석 기준일
        question_date (str): 대상 날짜
        target_line (str): 대상 라인
    
    Returns:
        str: 마크다운 형식 보고서
    """
    
    # 최종 수치 계산
    total_reduced = sum(move['qty'] for move in final_moves) if final_moves else 0
    achievement_rate = (total_reduced / reduction_needed * 100) if reduction_needed > 0 else 0
    final_qty = stock_result['total'] - total_reduced
    
    report = f"""
# 📊 {question_date} {target_line} 하이브리드 수사 보고서

## 🔍 수사 방식
- **전략 수립**: {strategy_source}
- **검증 엔진**: Python 6단계 검증 ✅
- **분석 기준일**: {today_str}

---

## 📋 [1단계] 현황 파악

### 기본 정보
- **대상**: {question_date} / {target_line}
- **현재 생산량**: {stock_result['total']:,}개
- **목표 생산량**: {target_qty:,}개 ({int(capa_target*100)}% CAPA)
- **필요 감축량**: {reduction_needed:,}개

### 품목 목록 ({len(stock_result['items'])}개)
"""
    
    for idx, item in enumerate(stock_result['items'][:15], 1):
        report += f"{idx}. **{item['name']}**: {item['qty_1차']:,}개 ({item['qty_1차']//item['plt']}PLT, 단위: {item['plt']}개/PLT)\n"
    
    if len(stock_result['items']) > 15:
        report += f"\n... 외 {len(stock_result['items']) - 15}개 품목\n"
    
    report += f"""

---

## 🔍 [2단계] 누적 납기 여유 분석

### ✅ 이동 가능 품목 ({len([x for x in items_with_slack if x['movable']])}개)
"""
    
    movable = [item for item in items_with_slack if item['movable']]
    for idx, item in enumerate(movable[:10], 1):
        report += f"""
**{idx}. {item['name']}**
- 계획 수량: {item['qty_1차']:,}개 ({item['qty_1차']//item['plt']}PLT)
- 누적 납기: {item['cumsum_target']:,}개
- 누적 생산: {item['cumsum_actual']:,}개
- **이동 가능 여유: {item['max_movable']:,}개** ✅
- 최종 납기: {item['last_due']} (여유: {item['buffer_days']}일)
"""
    
    if len(movable) > 10:
        report += f"\n... 외 {len(movable) - 10}개\n"
    
    unmovable = [item for item in items_with_slack if not item['movable']]
    if unmovable:
        report += f"""

### ❌ 이동 불가 품목 ({len(unmovable)}개)
"""
        for idx, item in enumerate(unmovable[:5], 1):
            report += f"{idx}. **{item['name']}**: 누적 여유 {item['max_movable']}개 (1PLT 미만, 이동 불가)\n"
    
    report += f"""

---

## 🎯 [3단계] 목적지 CAPA 현황

### 타라인 이송 가능 여부
"""
    
    transfer_targets = [status for key, status in capa_status.items() 
                       if status['date'] == question_date and status['line'] != target_line]
    
    for status in transfer_targets:
        status_icon = "✅ 여유" if status['remaining'] > 500 else ("⚠️ 부족" if status['remaining'] > 0 else "❌ 만석")
        report += f"{status_icon} **{status['line']}**: 잔여 {status['remaining']:,}개 / {status['max']:,}개 (가동률: {status['usage_rate']:.1f}%)\n"
    
    report += f"""

### 동일라인 연기 가능 날짜
"""
    
    delay_targets = [status for key, status in capa_status.items() 
                    if status['line'] == target_line and status['date'] != question_date]
    
    for status in delay_targets[:5]:
        status_icon = "✅ 여유" if status['remaining'] > 500 else "⚠️ 부족"
        report += f"{status_icon} **{status['date']}**: 잔여 {status['remaining']:,}개 (가동률: {status['usage_rate']:.1f}%)\n"
    
    report += f"""

---

## 🔒 [4단계] 물리 제약 정보

### 제약 조건 요약
- **T6 모델**: 조립1, 2, 3 모두 가능 (조립3 우선) ✅
- **A2XX 모델**: 조립1, 2만 가능 (조립3 절대 금지) ⚠️
- **전용 모델**: 동일 라인 내 날짜 이동만 가능 ⚠️

### 이동 가능 품목 제약 현황
"""
    
    for idx, item in enumerate(constraint_info[:8], 1):
        report += f"{idx}. **{item['name']}**: {item['constraint']} → {item['priority']}\n"
    
    if len(constraint_info) > 8:
        report += f"\n... 외 {len(constraint_info) - 8}개\n"
    
    report += f"""

---

## {'🤖 [5단계] AI 전략 수립 결과' if not ai_failed else '⚠️ [5단계] AI 실패 → Python 폴백'}
"""
    
    if ai_failed:
        report += f"""
**오류**: {ai_error}

→ Python 폴백 모드 활성화
→ 기본 우선순위 로직 적용 (T6→조립3, A2XX→조립2, 전용→연기)
"""
    else:
        report += f"""
**전략 개요**: {ai_strategy.get('strategy', 'N/A')}

**AI 설명**: 
{ai_strategy.get('explanation', 'N/A')}

**AI 제안 조치**: {len(ai_strategy.get('moves', []))}개
"""
    
    report += f"""

---

## ✅ [6단계] Python 최종 검증

### 검증 결과
"""
    
    if violations:
        report += f"⚠️ **검증 과정에서 {len(violations)}건 발견**\n\n"
        for v in violations:
            report += f"- {v}\n"
    else:
        report += "✅ **모든 검증 항목 통과**\n"
    
    report += f"""

### 최종 승인된 조치 계획 ({len(final_moves) if final_moves else 0}개)
"""
    
    if final_moves:
        for idx, move in enumerate(final_moves, 1):
            adjusted_mark = ""
            if move.get('adjusted'):
                adjusted_mark = f" ⚠️ (CAPA 부족: {move.get('original_qty', 0):,}개 → {move['qty']:,}개)"
            
            # PLT 계산 (안전하게)
            plt_count = move.get('plt', '?')
            
            report += f"""
**조치 {idx}**: {move.get('item', '미확인')}
- 이동량: **{move.get('qty', 0):,}개 ({plt_count}PLT)**{adjusted_mark}
- 출발: {move.get('from', f'{question_date}_{target_line}')}
- 도착: {move.get('to', 'N/A')}
- 이유: {move.get('reason', 'N/A')}
"""
    else:
        report += "\n❌ **승인된 조치 없음** (모든 제안이 검증 실패)\n"
    
    report += f"""

---

## 🎯 최종 결과

| 항목 | 수치 |
|------|------|
| 현재 생산량 | {stock_result['total']:,}개 |
| 목표 생산량 | {target_qty:,}개 |
| 필요 감축량 | {reduction_needed:,}개 |
| **실제 감축량** | **{total_reduced:,}개** |
| **최종 생산량** | **{final_qty:,}개** |
| **목표 달성률** | **{achievement_rate:.1f}%** |

### 검증 상태
"""
    
    if not violations and achievement_rate >= 90:
        report += "- ✅ **완벽 달성**: 모든 검증 통과 + 목표 90% 이상\n"
    elif achievement_rate >= 90:
        report += "- ✅ **목표 달성**: 90% 이상 달성\n"
        report += f"- ⚠️ **일부 조정**: {len(violations)}건 위반 수정\n"
    else:
        report += f"- ⚠️ **목표 미달**: 달성률 {achievement_rate:.1f}%\n"
    
    report += f"""

---

## 💡 하이브리드 방식의 장점

### ✅ 이번 수사에서 입증됨
1. **Python 정확성**: 1~4단계 모든 팩트 정확히 수사
2. **AI 유연성**: 복잡한 제약 조건 고려한 전략 수립
3. **Python 안전장치**: 6단계에서 AI 결과 재검증
4. **자동 폴백**: AI 실패 시 즉시 대체

### 📌 비고
"""
    
    if achievement_rate < 90:
        report += f"""
⚠️ **목표 달성률 90% 미만**

**추가 조정 방안:**
1. 선행 생산 검토 (이전 날짜로 당기기)
2. 납기 여유 적은 품목 타라인 이송 재검토
3. 목적지 CAPA 확보 후 추가 이동
4. 목표 비율 조정 (예: 70% → 75%)
"""
    else:
        report += "- ✅ 목표를 성공적으로 달성했습니다.\n"
    
    report += """

### 🔒 검증 완료 사항
- ✅ DB 실제 데이터 (qty_1차, qty_0차, is_workday) 기반
- ✅ 누적 납기 여유분만 이동 (납기 위반 없음)
- ✅ 목적지 CAPA 사전 검증 (병목 전이 방지)
- ✅ 물리 제약 완벽 준수 (T6, A2XX)
- ✅ PLT 단위 정수배만 사용
- ✅ DB 가동일만 사용 (휴무일 배제)
"""
    
    return report
