module saturate #(
    parameter IN_W = 128,
    parameter OUT_W = 32
)(
    input  wire signed [IN_W-1:0] i_data,
    output wire signed [OUT_W-1:0] o_data
);

    // Extend limits to IN_W
    wire signed [IN_W-1:0] max_val_ext =
        {{(IN_W-OUT_W){1'b0}}, {(OUT_W-1){1'b1}}, 1'b1};

    wire signed [IN_W-1:0] min_val_ext =
        {{(IN_W-OUT_W){1'b1}}, {(OUT_W-1){1'b0}}, 1'b0};

    assign o_data =
        (i_data > max_val_ext) ? max_val_ext[OUT_W-1:0] :
        (i_data < min_val_ext) ? min_val_ext[OUT_W-1:0] :
        i_data[OUT_W-1:0];

endmodule
