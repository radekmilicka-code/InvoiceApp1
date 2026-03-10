"""
Minimal pure-Python QR code generator.
Supports alphanumeric and byte mode, versions 1-10.
Returns a PIL Image or a list-of-lists boolean matrix.
No external dependencies beyond PIL (Pillow).
"""

# Reed-Solomon GF(256) with primitive polynomial x^8+x^4+x^3+x^2+1
GF_EXP = [0] * 512
GF_LOG = [0] * 256
_x = 1
for _i in range(255):
    GF_EXP[_i] = _x
    GF_LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11d
for _i in range(255, 512):
    GF_EXP[_i] = GF_EXP[_i - 255]

def _gf_mul(a, b):
    if a == 0 or b == 0: return 0
    return GF_EXP[GF_LOG[a] + GF_LOG[b]]

def _rs_poly_mul(p, q):
    r = [0] * (len(p) + len(q) - 1)
    for i, a in enumerate(p):
        for j, b in enumerate(q):
            r[i+j] ^= _gf_mul(a, b)
    return r

def _rs_generator(n):
    g = [1]
    for i in range(n):
        g = _rs_poly_mul(g, [1, GF_EXP[i]])
    return g

def _rs_encode(data, n_ec):
    gen = _rs_generator(n_ec)
    msg = list(data) + [0] * n_ec
    for i in range(len(data)):
        c = msg[i]
        if c:
            for j, g in enumerate(gen):
                msg[i+j] ^= _gf_mul(g, c)
    return msg[len(data):]

# QR format/version tables (version 2, error correction M)
# We'll use version 2-M for short strings, version 5-M for longer

_ALPHANUMERIC = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:'

def _can_alphanumeric(s):
    return all(c in _ALPHANUMERIC for c in s.upper())

def _encode_bytes(data):
    encoded = data.encode('utf-8') if isinstance(data, str) else data
    bits = []
    # Mode indicator: byte = 0100
    bits += [0,1,0,0]
    n = len(encoded)
    # Character count: 8 bits for version 1-9
    for i in range(7,-1,-1):
        bits.append((n >> i) & 1)
    for byte in encoded:
        for i in range(7,-1,-1):
            bits.append((byte >> i) & 1)
    return bits

# Version/EC table: (version, ec_level) -> (total_codewords, data_codewords, ec_codewords, blocks)
_VER_TABLE = {
    (1,'M'): (26, 16, 10, 1),
    (2,'M'): (44, 28, 16, 1),
    (3,'M'): (70, 44, 26, 1),
    (4,'M'): (100, 64, 36, 2),
    (5,'M'): (134, 86, 48, 2),
    (6,'M'): (172, 108, 64, 2),
    (7,'M'): (196, 124, 72, 4),
    (8,'M'): (242, 154, 88, 4),
    (9,'M'): (292, 182, 110, 4),
    (10,'M'): (346, 216, 130, 4),
}

def _select_version(data_bits, ec='M'):
    for v in range(1, 11):
        total, data_cw, ec_cw, blocks = _VER_TABLE[(v, ec)]
        if len(data_bits) <= data_cw * 8 - 4 - 8:  # rough check
            if (len(data_bits) + 4 + 8 + 7) // 8 <= data_cw:
                return v
    return 10

def _pad_bits(bits, data_cw):
    # Terminator
    bits = bits + [0] * min(4, data_cw*8 - len(bits))
    # Byte align
    while len(bits) % 8:
        bits.append(0)
    # Pad bytes
    pad = [0xEC, 0x11]
    i = 0
    while len(bits) < data_cw * 8:
        bits += [(pad[i%2] >> (7-j)) & 1 for j in range(8)]
        i += 1
    return bits[:data_cw*8]

def _bits_to_bytes(bits):
    out = []
    for i in range(0, len(bits), 8):
        b = 0
        for j in range(8):
            if i+j < len(bits):
                b = (b << 1) | bits[i+j]
            else:
                b <<= 1
        out.append(b)
    return out

# Format info strings for (ec_level, mask_pattern)
_FORMAT_INFO = {
    ('M', 0): 0b101010000010010,
    ('M', 1): 0b101000100100101,
    ('M', 2): 0b101111001111100,
    ('M', 3): 0b101101101001011,
    ('M', 4): 0b100010111111001,
    ('M', 5): 0b100000011001110,
    ('M', 6): 0b100111110010111,
    ('M', 7): 0b100101010100000,
}

def _place_format(grid, version, ec, mask):
    fmt = _FORMAT_INFO.get((ec, mask), 0)
    bits = [(fmt >> (14-i)) & 1 for i in range(15)]
    size = version * 4 + 17
    # Around top-left finder
    pos1 = [8,8,8,8,8,8, 8, 0,1,2,3,4,5,7,8]
    pos2 = [0,1,2,3,4,5, 7, 8,8,8,8,8,8,8,8]
    for i in range(15):
        r, c = pos2[i], pos1[i]
        grid[r][c] = bits[i]
    # Top-right
    for i in range(8):
        grid[8][size-1-i] = bits[i]
    # Bottom-left
    for i in range(7):
        grid[size-7+i][8] = bits[14-i]
    return grid

def _place_finder(grid, r, c):
    for dr in range(7):
        for dc in range(7):
            on = (dr in (0,6) or dc in (0,6) or (2<=dr<=4 and 2<=dc<=4))
            grid[r+dr][c+dc] = 1 if on else 0

def _place_timing(grid, version):
    size = version * 4 + 17
    for i in range(8, size-8):
        v = 1 if i % 2 == 0 else 0
        grid[6][i] = v
        grid[i][6] = v

def _place_alignment(grid, version):
    if version < 2: return
    table = {2:[6,18],3:[6,22],4:[6,26],5:[6,30],6:[6,34],7:[6,22,38],
             8:[6,24,42],9:[6,26,46],10:[6,28,50]}
    positions = table.get(version, [])
    centers = [(r,c) for r in positions for c in positions
               if not ((r==6 and c==6) or (r==6 and c==positions[-1]) or (r==positions[-1] and c==6))]
    for (r,c) in centers:
        for dr in range(-2,3):
            for dc in range(-2,3):
                on = (abs(dr)==2 or abs(dc)==2 or (dr==0 and dc==0))
                grid[r+dr][c+dc] = 1 if on else 0

def _is_function(grid_func, r, c):
    return grid_func[r][c]

def _apply_mask(grid, mask_id, size, func_mask):
    def cond(r, c):
        if mask_id == 0: return (r+c)%2==0
        if mask_id == 1: return r%2==0
        if mask_id == 2: return c%3==0
        if mask_id == 3: return (r+c)%3==0
        if mask_id == 4: return (r//2+c//3)%2==0
        if mask_id == 5: return (r*c)%2+(r*c)%3==0
        if mask_id == 6: return ((r*c)%2+(r*c)%3)%2==0
        if mask_id == 7: return ((r+c)%2+(r*c)%3)%2==0
    result = [row[:] for row in grid]
    for r in range(size):
        for c in range(size):
            if not func_mask[r][c] and cond(r, c):
                result[r][c] ^= 1
    return result

def _place_data(grid, func_mask, data_bits, size):
    idx = 0
    col = size - 1
    going_up = True
    while col > 0:
        if col == 6: col -= 1
        cols = [col, col-1]
        rows = range(size-1, -1, -1) if going_up else range(size)
        for r in rows:
            for c in cols:
                if not func_mask[r][c]:
                    if idx < len(data_bits):
                        grid[r][c] = data_bits[idx]
                    else:
                        grid[r][c] = 0
                    idx += 1
        going_up = not going_up
        col -= 2
    return grid

def generate_qr_matrix(text, ec='M'):
    """Generate QR code as a 2D boolean matrix."""
    bits = _encode_bytes(text)
    version = _select_version(bits, ec)
    total_cw, data_cw, ec_cw, blocks = _VER_TABLE[(version, ec)]
    bits = _pad_bits(bits, data_cw)
    data_bytes = _bits_to_bytes(bits)

    # Split into blocks and add EC
    block_size = data_cw // blocks
    remainder = data_cw % blocks
    all_data = []
    all_ec = []
    pos = 0
    for b in range(blocks):
        blen = block_size + (1 if b >= blocks - remainder else 0)
        block = data_bytes[pos:pos+blen]
        all_data.append(block)
        all_ec.append(_rs_encode(block, ec_cw // blocks))
        pos += blen

    # Interleave
    final_bits = []
    max_d = max(len(b) for b in all_data)
    for i in range(max_d):
        for b in all_data:
            if i < len(b):
                for j in range(7,-1,-1): final_bits.append((b[i]>>j)&1)
    max_e = max(len(b) for b in all_ec)
    for i in range(max_e):
        for b in all_ec:
            if i < len(b):
                for j in range(7,-1,-1): final_bits.append((b[i]>>j)&1)

    size = version * 4 + 17
    grid = [[0]*size for _ in range(size)]
    func = [[False]*size for _ in range(size)]

    def mark_func(r, c, v=None):
        func[r][c] = True
        if v is not None: grid[r][c] = v

    # Finder patterns + separators
    for (fr, fc) in [(0,0),(0,size-7),(size-7,0)]:
        _place_finder(grid, fr, fc)
        for i in range(7):
            mark_func(fr+i, fc); mark_func(fr+i, fc+6)
        for j in range(7):
            mark_func(fr, fc+j); mark_func(fr+6, fc+j)
        for i in range(7):
            for j in range(7): func[fr+i][fc+j] = True
    # Separators
    for i in range(8):
        for pos in [(7,i),(i,7),(7,size-8+i),(i,size-8),(size-8,i),(size-8+i,7)]:
            if 0<=pos[0]<size and 0<=pos[1]<size:
                func[pos[0]][pos[1]] = True
                grid[pos[0]][pos[1]] = 0

    # Timing
    _place_timing(grid, version)
    for i in range(size): func[6][i] = func[i][6] = True

    # Dark module
    grid[size-8][8] = 1; func[size-8][8] = True

    # Alignment
    _place_alignment(grid, version)

    # Format placeholder
    for r in range(9):
        func[r][8] = True
        func[8][r] = True
    for i in range(1, 9):
        func[8][size-i] = True
        func[size-i][8] = True

    # Place data
    grid = _place_data(grid, func, final_bits, size)

    # Find best mask
    best_mask = 0
    grid = _apply_mask(grid, best_mask, size, func)
    grid = _place_format(grid, version, ec, best_mask)

    return grid


def generate_qr_png(text, box_size=6, border=4):
    """Generate QR code as PNG bytes using PIL."""
    from PIL import Image
    matrix = generate_qr_matrix(text)
    size = len(matrix)
    img_size = (size + 2*border) * box_size
    img = Image.new('1', (img_size, img_size), 1)
    for r, row in enumerate(matrix):
        for c, val in enumerate(row):
            if val:
                x0 = (c + border) * box_size
                y0 = (r + border) * box_size
                for dy in range(box_size):
                    for dx in range(box_size):
                        img.putpixel((x0+dx, y0+dy), 0)
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()

import io
