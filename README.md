# VPP Digital Twin — Team 2

이산사건시스템(SimPy) 기반 3D 프린팅 파운드리 Digital Twin 구축 프로젝트
(캡스톤디자인, 참여기업: ㈜링크솔루션)

## 팀 정보

| 항목 | 내용 |
|---|---|
| 대상 공정 | VPP (Vat Photopolymerization) |
| 주요 소재 | Resin |
| 주요 후공정 | Part Removal, Washing, UV Curing, Support Removal, Surface Treatment, Inspection |

## 개발 환경

Python · SimPy · Streamlit/Plotly · Pandas · GitHub

## Process Route (기본안)

Order Reception → Job Assignment → Build Preparation/Batch Formation → VPP Build →
Part Removal → Washing → UV Curing → Support Removal → Surface Treatment → Inspection → Packaging

> 기업 인터뷰 결과에 따라 수정 예정

## 폴더 구조

/src/model, /entities, /resources, /scheduler, /logger, /validation
/dashboard  /replay  /tests  /data  /config  /docs  /results

## 목표

Validated SimPy-Based Digital Twin of VPP Foundry Operations

## 참고

기업 원본 자료 및 보안 필요 자료는 본 레포에 업로드하지 않습니다.

## 현재 버전

v0.0.1 (개발 초기 단계)

## 설치 방법

git clone https://github.com/gugc29311/team2-vpp-digital-twin.git
cd team2-vpp-digital-twin
pip install -r requirements.txt

## 실행 방법

(추후 작성 예정)
