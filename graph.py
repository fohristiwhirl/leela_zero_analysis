# This script extracts a winrate graph from an lza.py-produced SGF file.

import sys, time
import gofish
import matplotlib.pyplot as plt

node = gofish.load(sys.argv[1])
winrates = []

while 1:
	val = node.get_value("SBKV")
	try:
		winrates.append(float(val))
	except:
		winrates.append(None)

	node = node.main_child()
	if node == None:
		break

if len(winrates) < 2:
	print(s)
	time.sleep(0.5)
	sys.exit()

plt.style.use("dark_background")
_, ax = plt.subplots()
ax.spines["right"].set_visible(False)
ax.spines["top"].set_visible(False)
plt.xlim([0, len(winrates)])
plt.ylim([0, 100])
plt.yticks([0,25,50,75,100])
plt.xlabel("Move")
plt.ylabel("Black win %")
plt.plot(winrates)
plt.show()
