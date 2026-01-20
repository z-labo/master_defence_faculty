# app.py
import os
import json
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from flask_cors import CORS
import dropbox

# -----------------------------
# Config
# -----------------------------
DROPBOX_TOKEN = os.environ.get("DROPBOX_TOKEN")  # Render의 Environment에 설정
DROPBOX_BASE_FOLDER = os.environ.get("DROPBOX_BASE_FOLDER", "/Scoring")  # 기본값
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "https://z-labo.github.io"  # GitHub Pages 도메인
).split(",")

if not DROPBOX_TOKEN:
    raise RuntimeError("환경변수 DROPBOX_TOKEN 이 설정되어 있지 않습니다.")

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": [o.strip() for o in ALLOWED_ORIGINS]}})

def get_dbx() -> dropbox.Dropbox:
    return dropbox.Dropbox(DROPBOX_TOKEN)

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def validate_payload(payload: dict) -> tuple[bool, str]:
    # 최소 검증 (서버가 깨지지 않게)
    if not isinstance(payload, dict):
        return False, "payload must be a JSON object"
    if "judgeId" not in payload or not isinstance(payload["judgeId"], str) or not payload["judgeId"].strip():
        return False, "judgeId is required"
    if "results" not in payload or not isinstance(payload["results"], list) or len(payload["results"]) == 0:
        return False, "results must be a non-empty list"

    for r in payload["results"]:
        if not isinstance(r, dict):
            return False, "each result must be an object"
        if "participantId" not in r or not isinstance(r["participantId"], str) or not r["participantId"].strip():
            return False, "participantId is required"
        if "score" not in r:
            return False, "score is required"
        score = r["score"]
        if not isinstance(score, int) or score < 0 or score > 5:
            return False, "score must be an integer 0..5"
        # comment는 선택
        if "comment" in r and r["comment"] is not None and not isinstance(r["comment"], str):
            return False, "comment must be a string"

    return True, ""

@app.get("/health")
def health():
    return jsonify({"ok": True, "time": utc_now_iso()})

@app.post("/submit_vote")
def submit_vote():
    payload = request.get_json(silent=True)

    ok, msg = validate_payload(payload)
    if not ok:
        return jsonify({"error": msg}), 400

    judge_id = payload["judgeId"].strip()

    # 저장 파일명: judgeId별로 덮어쓰기(HTML의 "final vote만" 정책과 일치)
    # 예: /Scoring/vote_results/J1.json
    folder = f"{DROPBOX_BASE_FOLDER.rstrip('/')}/vote_results"
    dropbox_path = f"{folder}/{judge_id}.json"

    # 서버에서 저장 시각을 별도로 찍어두면 추후 감사/디버깅에 유리
    payload_server = dict(payload)
    payload_server["serverReceivedAt"] = utc_now_iso()

    data_bytes = json.dumps(payload_server, ensure_ascii=False, indent=2).encode("utf-8")

    try:
        dbx = get_dbx()
        dbx.files_upload(
            data_bytes,
            dropbox_path,
            mode=dropbox.files.WriteMode.overwrite,
            mute=True
        )
    except Exception as e:
        # Render 로그에 에러가 남도록 문자열 포함
        return jsonify({"error": "dropbox upload failed", "detail": str(e)}), 500

    return jsonify({"ok": True, "path": dropbox_path})

if __name__ == "__main__":
    # 로컬 테스트용
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
