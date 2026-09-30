# -*- coding: utf-8 -*-
"""
프린터 대기열 우선순위 규칙 (디스패칭).

프린터 대기열은 simpy.PriorityResource — priority 값이 **작을수록 먼저** 출력한다.
값이 같으면 요청(도착) 순서대로 (SimPy 기본 동작).

  FCFS : 프린터 요청 순번 (먼저 요청한 배치 먼저)
  SPT  : 출력시간이 짧은 배치 먼저
  EDD  : 배치 안에서 가장 이른 납기가 빠른 배치 먼저
  URGENT_FIRST : 긴급 주문 부품이 든 배치 먼저, 같은 등급 안에서는 FCFS
                 (배치 형성은 그대로 — 긴급 부품도 배치가 확정될 때까지는 기다림)
                 ⚠️ 현재 가정값(배치당 약 28부품)에서는 긴급 10%만 돼도 배치의 약 98%에 긴급 부품이 섞여
                    사실상 FCFS 와 같다 (Rush Order seed 42: 모든 KPI 동일). 효과를 보려면 배치 형성 단계에서
                    긴급 부품을 분리해야 함.

새 규칙 추가: RULES 에 이름 -> 함수(batch, seq) 를 넣으면 설정 검사(SimConfig.validate)에도 자동 반영된다.
"""

URGENT_OFFSET = 10 ** 9     # 요청 순번보다 항상 큰 값 -> 긴급 배치는 일반 배치보다 항상 앞

RULES = {
    "FCFS": lambda b, seq: seq,
    "SPT": lambda b, seq: b.build_time,
    "EDD": lambda b, seq: b.earliest_due,
    "URGENT_FIRST": lambda b, seq: seq - (URGENT_OFFSET if b.has_urgent else 0),
}


def priority(rule, batch, seq):
    """규칙 이름 -> 프린터 대기열 priority 값. seq = 프린터 요청 순번(1부터)."""
    return RULES[rule](batch, seq)
