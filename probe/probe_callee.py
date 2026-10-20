# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""Phase 0 probe callee. NOT production code."""

from genlayer import gl, DynArray, TreeMap


class ProbeCallee(gl.Contract):
	storage: TreeMap[str, str]
	calls: DynArray[str]

	def __init__(self, salt: str):
		self.storage["salt"] = salt

	@gl.public.view
	def get_salt(self) -> str:
		return self.storage["salt"]

	@gl.public.view
	def call_count(self) -> int:
		return len(self.calls)

	@gl.public.write
	def record(self, action_id: str, note: str) -> str:
		self.calls.append(action_id)
		self.storage["last"] = note
		return note