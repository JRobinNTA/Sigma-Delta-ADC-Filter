module comp_fir #(
    parameter IW = 32,
    parameter OW = 32,
    parameter COEFF_W = 16
)(
    input  wire                 clk,
    input  wire                 rst,
    input  wire                 i_valid,
    input  wire signed [IW-1:0] i_data,
    output reg                  o_valid,
    output reg  signed [OW-1:0] o_data
);

    // Symmetric coefficients (Q15)
    localparam signed [COEFF_W-1:0] C0  = -16'd358;
    localparam signed [COEFF_W-1:0] C1  = -16'd190;
    localparam signed [COEFF_W-1:0] C2  =  16'd1668;
    localparam signed [COEFF_W-1:0] C3  =  16'd1248;
    localparam signed [COEFF_W-1:0] C4  = -16'd6596;
    localparam signed [COEFF_W-1:0] C5  = -16'd6077;
    localparam signed [COEFF_W-1:0] C6  =  16'd16486;
    localparam signed [COEFF_W-1:0] C7  =  16'd32767;

    // Delay line
    reg signed [IW-1:0] delay_line [0:14];
    integer i;

    // -------- Width definitions --------
    localparam PRE_W = IW + 1;                 // pre-add width
    localparam MUL_W = PRE_W + COEFF_W;        // multiplier output
    localparam ACC_W = MUL_W + 3;              // sum of 8 terms

    // -------- Symmetric pre-adds --------
    wire signed [PRE_W-1:0] s0 = delay_line[0]  + delay_line[14];
    wire signed [PRE_W-1:0] s1 = delay_line[1]  + delay_line[13];
    wire signed [PRE_W-1:0] s2 = delay_line[2]  + delay_line[12];
    wire signed [PRE_W-1:0] s3 = delay_line[3]  + delay_line[11];
    wire signed [PRE_W-1:0] s4 = delay_line[4]  + delay_line[10];
    wire signed [PRE_W-1:0] s5 = delay_line[5]  + delay_line[9];
    wire signed [PRE_W-1:0] s6 = delay_line[6]  + delay_line[8];

    // -------- Multipliers --------
    wire signed [MUL_W-1:0] m0 = s0 * C0;
    wire signed [MUL_W-1:0] m1 = s1 * C1;
    wire signed [MUL_W-1:0] m2 = s2 * C2;
    wire signed [MUL_W-1:0] m3 = s3 * C3;
    wire signed [MUL_W-1:0] m4 = s4 * C4;
    wire signed [MUL_W-1:0] m5 = s5 * C5;
    wire signed [MUL_W-1:0] m6 = s6 * C6;

    // center tap uses original IW width
    wire signed [MUL_W-1:0] m7 = delay_line[7] * C7;

    // -------- Accumulator --------
    wire signed [ACC_W-1:0] acc =
        {{(ACC_W-MUL_W){m0[MUL_W-1]}}, m0} +
        {{(ACC_W-MUL_W){m1[MUL_W-1]}}, m1} +
        {{(ACC_W-MUL_W){m2[MUL_W-1]}}, m2} +
        {{(ACC_W-MUL_W){m3[MUL_W-1]}}, m3} +
        {{(ACC_W-MUL_W){m4[MUL_W-1]}}, m4} +
        {{(ACC_W-MUL_W){m5[MUL_W-1]}}, m5} +
        {{(ACC_W-MUL_W){m6[MUL_W-1]}}, m6} +
        {{(ACC_W-MUL_W){m7[MUL_W-1]}}, m7};

    // -------- Rounding + shift --------
    localparam SHIFT = 15;

    wire signed [ACC_W-1:0] rounded =
        acc + (1 <<< (SHIFT-1));

    wire signed [ACC_W-1:0] shifted =
        rounded >>> SHIFT;

    // -------- Output stage --------
    always @(posedge clk) begin
        if (rst) begin
            for (i=0; i<15; i=i+1)
                delay_line[i] <= 0;

            o_data  <= 0;
            o_valid <= 0;

        end else if (i_valid) begin
            // shift register
            delay_line[0] <= i_data;
            for (i=1; i<15; i=i+1)
                delay_line[i] <= delay_line[i-1];

            // truncate to output width
            o_data  <= shifted[OW-1:0];
            o_valid <= 1'b1;

        end else begin
            o_valid <= 1'b0;
        end
    end

endmodule
