import os
import json
from datetime import datetime, timezone, timedelta
JST = timezone(timedelta(hours=9))

# collection
from collections import defaultdict

from flask import Flask, request, jsonify
import dropbox

from flask_cors import CORS  # ★ 추가

app = Flask(__name__)

# ★ CORS 설정: GitHub Pages 도메인만 허용 (테스트용으로는 "*" 도 가능)
CORS(app, resources={r"/*": {"origins": "https://z-labo.github.io"}})


# 환경변수에서 Dropbox 토큰 읽기
DROPBOX_TOKEN = os.environ.get("DROPBOX_TOKEN")
DROPBOX_BASE_FOLDER = "/vote_results"  # Dropbox 안의 저장 폴더 (미리 하나 만들어 두는 것을 권장)

if not DROPBOX_TOKEN:
  raise RuntimeError("환경변수 DROPBOX_TOKEN 이 설정되어 있지 않습니다.")

# Dropbox 클라이언트 (필요할 때마다 새로 만들어도 되고, 전역으로 써도 됨)
def get_dbx():
  return dropbox.Dropbox(DROPBOX_TOKEN)

def load_all_votes_from_dropbox():
  """
  /vote_results 폴더 아래의 모든 *.json 파일을 읽어서
  JSON 객체 리스트로 반환.
  """
  dbx = get_dbx()
  records = []

  # 폴더 목록 가져오기
  res = dbx.files_list_folder(DROPBOX_BASE_FOLDER)
  entries = list(res.entries)
  while res.has_more:
    res = dbx.files_list_folder_continue(res.cursor)
    entries.extend(res.entries)

  for e in entries:
    # 파일만 대상으로, 확장자가 .json 인 것만
    if isinstance(e, dropbox.files.FileMetadata) and e.name.lower().endswith(".json"):
      try:
        meta, resp = dbx.files_download(e.path_lower)
        content = resp.content.decode("utf-8")
        data = json.loads(content)
        records.append(data)
      except Exception as ex:
        print("JSON parse error:", e.path_lower, repr(ex))
        continue

  return records

def aggregate_votes(records):
  """
  records: load_all_votes_from_dropbox()가 반환한 JSON 객체 리스트
  return: 집계 결과 딕셔너리
  """
  # (judgeId, participantId) → (timestamp(str), score, comment)
  latest = {}

  for rec in records:
    judge_id = rec.get("judgeId")
    ts = rec.get("timestamp") or ""
    results = rec.get("results") or []

    if not judge_id:
      continue

    # timestamp 비교는 ISO8601 문자열 기준으로도 시간 순서가 맞는다고 가정
    for entry in results:
      pid = entry.get("participantId")
      score = entry.get("score")
      comment = entry.get("comment") or ""

      if not pid:
        continue

      key = (judge_id, pid)
      prev = latest.get(key)
      if (prev is None) or (ts > prev[0]):
        latest[key] = (ts, score, comment)

  # 참가자별 집계
  participants = {}
  for (judge_id, pid), (ts, score, comment) in latest.items():
    if score is None:
      continue
    try:
      s = float(score)
    except Exception:
      continue

    p = participants.setdefault(pid, {
      "participantId": pid,
      "totalScore": 0.0,
      "voteCount": 0,
      "details": []  # 각 심사위원별 상세
    })
    p["totalScore"] += s
    p["voteCount"] += 1
    p["details"].append({
      "judgeId": judge_id,
      "score": s,
      "comment": comment,
      "timestamp": ts
    })

  # 평균 및 정렬
  result_list = []
  for pid, info in participants.items():
    cnt = info["voteCount"]
    avg = info["totalScore"] / cnt if cnt > 0 else 0.0
    info["avgScore"] = round(avg, 3)
    result_list.append(info)

  # 평균 점수 내림차순, 동률이면 voteCount 많은 순
  result_list.sort(key=lambda x: (-x["avgScore"], -x["voteCount"], x["participantId"]))

  return {
    "ok": True,
    "lastUpdated": datetime.now(timezone.utc).isoformat(),
    "participants": result_list
  }


@app.route("/submit_vote", methods=["POST", "OPTIONS"]) 
def submit_vote():
  # 0) 브라우저 preflight(OPTIONS) 요청 처리
  if request.method == "OPTIONS":
    # flask-cors가 헤더는 달아주므로, 상태코드만 204로 리턴
    return ("", 204)
  
  # 1) JSON 받아오기
  try:
    data = request.get_json(force=True)
  except Exception:
    return jsonify({"ok": False, "error": "invalid_json"}), 400

  # 2) 최소한의 검증
  judge_id = data.get("judgeId")
  results = data.get("results")

  if not judge_id or not isinstance(results, list):
    return jsonify({"ok": False, "error": "bad_payload"}), 400

  # 3) 파일 이름/경로 만들기

  now_jst = datetime.now(JST)
  date_str = now_jst.strftime("%Y%m%d")  # 예: "20251117"

  '''
  #    예: J1_2025-11-17T09-00-00Z.json
  ts = data.get("timestamp")
  if ts is None:
    ts = datetime.now(timezone.utc).isoformat()

  safe_ts = ts.replace(":", "-")  # Windows/Dropbox 경로에 안전하게
  filename = f"{judge_id}_{safe_ts}.json"
  '''
  filename = f"{judge_id}_{date_str}.json"
  dropbox_path = f"{DROPBOX_BASE_FOLDER}/{filename}"

  # 4) Dropbox에 업로드
  dbx = get_dbx()
  try:
    # JSON 문자열로 인코딩 (UTF-8)
    content = json.dumps(data, ensure_ascii=False, indent=2)
    dbx.files_upload(
      content.encode("utf-8"),
      dropbox_path,
      mode=dropbox.files.WriteMode.overwrite  # 같은 이름 있으면 에러, 덮어쓰려면 .overwrite
    )
  except Exception as e:
    # 로그용으로 출력(호스팅 서비스의 로그에서 확인)
    print("Dropbox upload error:", repr(e))
    return jsonify({"ok": False, "error": "dropbox_upload_failed"}), 500

  # 5) 클라이언트에 성공 응답
  return jsonify({"ok": True, "path": dropbox_path})

@app.after_request
def add_cors_headers(response):
    # GitHub Pages 도메인만 허용 (테스트용이면 "*" 도 가능)
    response.headers["Access-Control-Allow-Origin"] = "https://z-labo.github.io"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

@app.route("/api/results", methods=["GET"])
def api_results():
  try:
    records = load_all_votes_from_dropbox()
    agg = aggregate_votes(records)
    return jsonify(agg)
  except Exception as e:
    print("Aggregate error:", repr(e))
    return jsonify({
      "ok": False,
      "error": "aggregate_failed",
      "detail": repr(e)     # ★ 디버깅용 상세 메시지
    }), 500