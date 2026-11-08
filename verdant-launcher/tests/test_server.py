from fastapi.testclient import TestClient
from verdant.server import create_app

INVITE = "a-private-invitation-secret-123456"


def identity(client,name):
    data = client.post("/register",json={"name":name,"invite":INVITE}).json()
    return data,{"Authorization":"Bearer "+data["token"]}


def test_world_visibility_requires_mutual_friendship(tmp_path):
    with TestClient(create_app(tmp_path/"test.db",INVITE)) as client:
        alice,a = identity(client,"Alice")
        bob,b = identity(client,"Bob")
        eve,e = identity(client,"Eve")
        world = {"address":"localhost:25565", "manifest":{"name":"World","minecraft":"1.21.1","loader":"vanilla"}}
        assert client.put("/world",json=world,headers=a).status_code == 200
        assert client.get("/friends").status_code == 401
        assert client.get("/friends",headers=e).json()["friends"] == []
        assert client.post("/friends/request",json={"code":bob["code"]},headers=a).status_code == 200
        assert client.get("/friends",headers=b).json()["friends"] == []
        assert client.post(f"/friends/{alice['id']}/accept",headers=e).status_code == 404
        assert client.post(f"/friends/{alice['id']}/accept",headers=b).status_code == 200
        friend = client.get("/friends",headers=b).json()["friends"][0]
        assert friend["world"]["address"] == "localhost:25565"
        assert "token_hash" not in friend
        assert client.get("/friends",headers=e).json()["friends"] == []
        assert client.delete(f"/friends/{alice['id']}",headers=b).status_code == 200
        assert client.get("/friends",headers=b).json()["friends"] == []


def test_registration_invite_and_world_validation(tmp_path):
    with TestClient(create_app(tmp_path/"test.db",INVITE)) as client:
        assert client.post("/register",json={"name":"Alice","invite":"wrong"}).status_code == 403
        _,a = identity(client,"Alice")
        assert client.put("/world",json={"address":"bad:99999","manifest":{"name":"x","minecraft":"1.21.1","loader":"vanilla"}},headers=a).status_code == 422
        assert client.post("/heartbeat",headers=a).status_code == 200
        assert client.delete("/me",headers=a).status_code == 200
        assert client.get("/me",headers=a).status_code == 401


def test_large_request_rejected(tmp_path):
    with TestClient(create_app(tmp_path/"test.db",INVITE)) as client:
        assert client.post("/register",content=b"x"*2_000_001).status_code == 413
