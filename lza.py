# As of April 2019, expects Leela Zero 0.17 or higher.

import gofish, json, os, subprocess, sys, threading, time

extras = "--gtp --noponder --resignpct 0 --threads 1"

config = None


class Progress:

	def __init__(self, units):
		self.units = units
		self.length = 0

	def erase(self):
		for n in range(self.length):
			print("\b", end = "")

	def update(self, units_done):
		self.erase()
		pc = int((units_done / self.units) * 100)
		msg = "Progress: {}%".format(pc)
		self.length = len(msg)
		print(msg, end = "")
		sys.stdout.flush()

	def finish(self):
		self.erase()
		print("Progress: 100%")


class Connection:

	def __init__(self, cmd):
		self.n = 0
		self.in_id = None		# Last incoming message ID seen (e.g. when the engine sends "=7" or whatnot)
		self.process = subprocess.Popen(cmd, shell = False, stdin = subprocess.PIPE, stdout = subprocess.PIPE, stderr = subprocess.DEVNULL)
		# Note that the stderr needs to be consumed somehow, hence the DEVNULL here.

	def _next_qid(self):
		self.n += 1
		return self.n

	def _send(self, msg):
		msg = msg.strip()
		# print(msg)
		msg = bytes(msg, encoding = "ascii")
		self.process.stdin.write(msg)
		self.process.stdin.write(b"\n")
		self.process.stdin.flush()

	def _receive(self):
		z = self.process.stdout.readline().decode("utf-8").strip()
		if len(z) > 0 and z[0] == "=":
			self.in_id = int(z[1:].split()[0])
		# print(z)
		return z

	def send_and_receive(self, msg):

		# Add a unique ID number to the start...

		out_id = self._next_qid()
		msg = "{} {}".format(out_id, msg)

		# Send...

		self._send(msg)

		# Receive the response, ignoring any lines with a different ID.

		s = ""

		while 1:
			z = self._receive()

			if self.in_id == out_id:
				if z.strip() != "":
					s += z + "\n"
				else:
					return s			# Blank line always means end of output (I think).

	def _get_lz_analysis_string(self, colour):

		# colour is "b" or "w".
		# We return the last info line sent by LZ.

		out_id = self._next_qid()
		msg = "{} lz-analyze {} interval 50".format(out_id, colour)

		self._send(msg)

		start_time = time.monotonic()

		s = ""							# The string to return.

		while time.monotonic() - start_time < config["seconds"]:
			z = self._receive()

			if self.in_id == out_id:
				if "info" in z:
					s = z

		# For synchronization purposes...
		self.send_and_receive("name")

		return s.strip()

	def get_lz_analysis(self, colour, boardsize):

		best_move, score_before_move, visits, PV = None, None, None, None

		s = self._get_lz_analysis_string(colour)

		'''
		info move D16 visits 41 winrate 4342 prior 1647 lcb 4291 order 0 pv D16 Q4 Q16 D4 R6 R14 C6 C14 F3 F4 info move D4 visits
		40 winrate 4341 prior 1637 lcb 4289 order 1 pv D4 Q16 Q4 D16 R14 R6 C14 C6 F17 F16 info move Q16 visits 40 winrate 4341
		prior 1626 lcb 4289 order 2 pv Q16 D4 D16 Q4 R6 R14 C6 C14 F3 F4
		'''

		if "info" not in s:
			return best_move, score_before_move, visits, PV

		moves = s.split("info")
		moves = [s.strip() for s in moves]
		moves = [s for s in moves if len(s) > 0]

		for move in moves:

			if "order 0" not in move:
				continue

			'''info move D16 visits 41 winrate 4342 prior 1647 lcb 4291 order 0 pv D16 Q4 Q16 D4 R6 R14 C6 C14 F3 F4'''

			fields = move.split()
			try:
				i = fields.index("move")
				best_move = gofish.point_from_english_string(fields[i + 1], boardsize)
			except:
				pass

			try:
				i = fields.index("winrate")
				wr = int(fields[i + 1]) / 100
				if colour == "w":
					wr = 100 - wr
				score_before_move = wr
			except:
				pass

			try:
				i = fields.index("visits")
				visits = int(fields[i + 1])
			except:
				pass

			try:
				i = fields.index("pv")
				pv = []
				for mv in fields[i + 1:]:
					point = gofish.point_from_english_string(mv, boardsize)
					if point is None:
						break
					pv.append(point)
				PV = pv
			except:
				pass

			break

		return best_move, score_before_move, visits, PV


class Info:

	# We'll store moves as either None or [x,y]

	def __init__(self, node):
		self.node = node				# gofish node
		self.colour = None				# "b" or "w"
		self.best_move = None
		self.PV = None					# PV alternative to the actual move, if any
		self.score_before_move = None
		self.score_after_move = None
		self.visits = None
		self.parent = None				# Info object of previous position

	def send_AB_AW(self, conn):

		for stone in self.node.get_all_values("AB"):
			english = gofish.english_string_from_string(stone, self.node.board.boardsize)
			conn.send_and_receive("play b {}".format(english))

		for stone in self.node.get_all_values("AW"):
			english = gofish.english_string_from_string(stone, self.node.board.boardsize)
			conn.send_and_receive("play w {}".format(english))

	def send_move(self, conn):

		if self.node.move_coords():
			english_actual = gofish.english_string_from_point(*self.node.move_coords(), self.node.board.boardsize)
			conn.send_and_receive("play {} {}".format(self.colour, english_actual))

	def node_markup(self):

		node = self.node

		if self.score_after_move != None:
			score_string = "{0:.2f}%".format(self.score_after_move)				# With % sign
			node.set_value("SBKV", "{0:.2f}".format(self.score_after_move))		# Without
		else:
			score_string = "??"

		if self.score_after_move != None and self.score_before_move != None:
			if self.best_move != node.move_coords():
				delta_string = "{0:.2f}%".format(self.score_after_move - self.score_before_move)
			else:
				delta_string = "( {0:.2f}% )".format(self.score_after_move - self.score_before_move)
		else:
			delta_string = "??"

		if self.best_move != node.move_coords() and self.best_move:
			prefer_string = "LZ prefers {}".format(gofish.english_string_from_point(*self.best_move, node.board.boardsize))
		else:
			prefer_string = ""

		if self.visits:
			visits_string = "Visits: {}".format(self.visits)
		else:
			visits_string = ""

		full_string = "{}\nDelta: {}\n{}\n\n{}".format(score_string, delta_string, prefer_string, visits_string).strip()

		node.add_to_comment_top(full_string)

		if self.score_after_move != None and self.score_before_move != None:
			if abs(self.score_after_move - self.score_before_move) > config["hotspot_threshold"]:
				node.set_value("HO", 1)

		if self.best_move:
			sgf_point = gofish.string_from_point(*self.best_move)
			node.add_value("TR", sgf_point)

		if self.best_move != node.move_coords():

			if self.parent and self.PV:

				first_colour = {"b": gofish.BLACK, "w": gofish.WHITE}[self.colour]
				made_first = False

				var_node = self.parent.node

				for point in self.PV:
					if made_first:
						var_node = var_node.try_move(*point)
					else:
						var_node = var_node.try_move(*point, colour = first_colour)
						made_first = True

	def analyze(self, conn):

		if self.colour not in ["b", "w"]:
			return

		self.best_move, self.score_before_move, self.visits, self.PV = conn.get_lz_analysis(self.colour, self.node.board.boardsize)


def main():

	global config

	if len(sys.argv) == 1:
		print("Usage: {} <filename>".format(sys.argv[0]))
		sys.exit()

	scriptpath = os.path.realpath(__file__)
	configfile = os.path.join(os.path.dirname(scriptpath), "config.json")
	with open(configfile) as cfg:
		config = json.load(cfg)

	cmd = '"{}" {} -w "{}"'.format(config["engine"], extras, os.path.join(config["network_dir"], config["network"]))

	print("Starting Leela Zero...")
	conn = Connection(cmd)
	conn.send_and_receive("name")			# Ensure we can communicate.
	print("Working.")

	root = gofish.load(sys.argv[1])

	# Make a list of Info objects...

	all_info = []
	node = root

	while 1:

		# Totally ignore empty nodes. Everything else gets put in the list...

		if "B" in node.properties or "W" in node.properties or "AB" in node.properties or "AW" in node.properties:

			new_info = Info(node)

			if len(all_info) > 0:
				new_info.parent = all_info[-1]		# Might not correspond to the node's actual parent node (due to empty nodes)

			if node.move_colour():
				new_info.colour = {gofish.BLACK: "b", gofish.WHITE: "w"}[node.move_colour()]

			all_info.append(new_info)

		node = node.main_child()
		if node == None:
			break

	# Main loop...

	save_time = time.monotonic()
	progress = Progress(len(all_info))
	progress.update(0)

	for n, info in enumerate(all_info):

		info.send_AB_AW(conn)
		info.analyze(conn)

		if info.parent:
			info.parent.score_after_move = info.score_before_move

		info.send_move(conn)

		if info.parent:
			info.parent.node_markup()

		if time.monotonic() - save_time > 10:
			root.save(sys.argv[1] + ".lza.sgf")
			save_time = time.monotonic()

		progress.update(n)

	# The final node needs its score_after_move before it can be marked up...

	colour = "w" if info.colour == "b" else "b"
	_, info.score_after_move, _, _ = conn.get_lz_analysis(colour, info.node.board.boardsize)
	info.node_markup()

	# Save and finish.

	root.save(sys.argv[1] + ".lza.sgf")
	progress.finish()


# -------------

main()
